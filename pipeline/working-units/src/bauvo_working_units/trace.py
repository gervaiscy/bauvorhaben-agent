"""Preserve raw provider attempts separately from application interpretations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
from httpx import TransportError
from .gemini import IncompleteResponse


def failure_details(exc):
    return {'error_type': type(exc).__name__, 'reason': getattr(exc, 'reason', None),
            'finish_reason': getattr(exc, 'finish_reason', None), 'http_code': getattr(exc, 'code', None)}


class ModelRequestFailed(RuntimeError):
    def __init__(self, attempts):
        self.attempts = attempts
        super().__init__('Model request failed; see preserved attempt evidence')


class RetryingModel:
    """At most two identical calls; wrap recorders so every attempt is preserved."""
    def __init__(self, model):
        self._model = model

    def extract(self, prompt):
        attempts = []
        for attempt in range(2):
            try:
                return self._model.extract(prompt)
            except Exception as exc:
                transient = getattr(exc, 'code', None) in (429, 500, 502, 503, 504) or isinstance(exc, (TimeoutError, TransportError))
                incomplete = isinstance(exc, IncompleteResponse)
                if not (transient or incomplete or isinstance(getattr(exc, 'code', None), int)):
                    raise
                attempts.append(failure_details(exc))
                if not (transient or incomplete):
                    raise ModelRequestFailed(attempts) from exc
                if attempt == 1:
                    raise ModelRequestFailed(attempts) from exc
                # Do not resend a provider-blocked generation or feed failed text back.
                if incomplete and exc.finish_reason in ('SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII'):
                    raise ModelRequestFailed(attempts) from exc
                print('Working-unit request incomplete/unavailable; retrying unchanged request once', flush=True)
                if transient:
                    time.sleep(1)


class TracingModel:
    def __init__(self, model, directory):
        self._model = model
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        if any(self._directory.glob('*-prompt.txt')) or any(self._directory.glob('*-response.json')):
            raise FileExistsError(f'Trace directory already has model calls: {self._directory}')
        self._call_number = 0

    def extract(self, prompt):
        self._call_number += 1
        stem = f'{self._call_number:04d}'
        (self._directory / f'{stem}-prompt.txt').write_text(prompt, encoding='utf-8')
        response = None
        try:
            response = self._model.extract(prompt)
            return response
        finally:
            raw = getattr(self._model, 'last_raw_response', None)
            metadata = getattr(self._model, 'last_attempt', None)
            if isinstance(raw, str) or (metadata and metadata['status'] == 'recorded'):
                (self._directory / f'{stem}-response.json').write_text(
                    raw if isinstance(raw, str) else json.dumps(response, ensure_ascii=False) + '\n', encoding='utf-8')
            if metadata is not None:
                (self._directory / f'{stem}-attempt.json').write_text(json.dumps(metadata, indent=2) + '\n')


class RawRunRecorder:
    """raw.json holds provider values; raw.attempts.json holds status/finish reasons."""
    def __init__(self, model, path):
        self._model = model
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._responses = []
        self._attempts = []
        self.last_raw_response = None
        self.last_attempt = None
        self._write()

    def _write(self):
        self._path.write_text(json.dumps(self._responses, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        self._path.with_suffix('.attempts.json').write_text(json.dumps(self._attempts, indent=2) + '\n', encoding='utf-8')

    def extract(self, prompt):
        self.last_raw_response = None
        response = None
        metadata = {'call': len(self._attempts) + 1, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                    'pass': prompt.split('.', 1)[0], 'status': 'recorded', 'raw_index': None}
        try:
            response = self._model.extract(prompt)
            return response
        except BaseException as exc:
            metadata.update(status='request_failed', **failure_details(exc))
            raise
        finally:
            raw = getattr(self._model, 'last_raw_response', None)
            self.last_raw_response = raw if isinstance(raw, str) else None
            if isinstance(raw, str):
                metadata['response_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
            metadata['finish_reason'] = getattr(self._model, 'last_finish_reason', None) or metadata.get('finish_reason')
            if isinstance(raw, str):
                try:
                    value = json.loads(raw)
                except ValueError:
                    value = raw
            else:
                value = response
            if raw is not None or metadata['status'] == 'recorded':
                metadata['raw_index'] = len(self._responses)
                self._responses.append(json.loads(json.dumps(value, ensure_ascii=False)))
            self.last_attempt = metadata
            self._attempts.append(metadata)
            self._write()


class RequestCheckpoints:
    """Replay hash-verified, exact timing requests without editing the previous run."""
    def __init__(self, model, directory, identity, resume_from=None, trace_directory=None):
        from .schemas import generation_for, RESPONSE_SCHEMAS
        self._model = model
        self.directory = Path(directory)
        self.identity = {**identity, 'generation': generation_for('PAGE_TIMING'),
                         'response_schema': RESPONSE_SCHEMAS['PAGE_TIMING']}
        self.required = RESPONSE_SCHEMAS['PAGE_TIMING']['properties']['assessments']['items']['required']
        self.saved = {}
        self.reused = 0
        self.unverified = 0
        if resume_from is not None:
            self._load(Path(resume_from))
        # Validate recovery before creating any new artifacts.
        self.directory.mkdir(parents=True, exist_ok=False)
        trace = Path(trace_directory) if trace_directory is not None else self.directory.parent / 'working-units.trace'
        manifest = {'version': 2, 'identity': self.identity,
                    'trace_directory': os.path.relpath(trace.resolve(), self.directory.resolve())}
        (self.directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        self.last_raw_response = None
        self.last_finish_reason = None
        if self.unverified:
            print(f'Timing recovery: {self.unverified} legacy records lack response hashes and will not be reused', flush=True)

    def _remember(self, prompt, raw, checksum):
        if not checksum:
            self.unverified += 1
            return
        if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != checksum:
            raise ValueError('Timing checkpoint response checksum mismatch')
        value = json.loads(raw)
        if (isinstance(value, dict) and isinstance(value.get('assessments'), list)
                and all(isinstance(row, dict) and all(key in row for key in self.required)
                        for row in value['assessments'])):
            self.saved[prompt] = raw

    def _load(self, source):
        # A run folder is convenient; a checkpoint directory or output path disambiguates siblings.
        if source.suffix == '.json':
            run_directory = source.parent
            candidates = [source.with_name(source.stem + '.checkpoints') / 'manifest.json']
            fallback_trace = source.with_name(source.stem + '.trace')
        else:
            run_directory = source
            direct = source / 'manifest.json'
            candidates = [direct] if direct.is_file() else sorted(source.glob('*.checkpoints/manifest.json'))
            fallback_trace = None
        candidates = [path for path in candidates if path.is_file()]
        if len(candidates) > 1:
            raise ValueError('Multiple timing outputs found; select an output path or checkpoint directory with --resume-from')
        manifest_path = candidates[0] if candidates else None
        if manifest_path is not None:
            manifest = json.loads(manifest_path.read_text())
            if manifest.get('version') != 2 or manifest.get('identity') != self.identity:
                raise ValueError('Checkpoint model/settings changed; cannot reuse timing requests')
            trace = manifest_path.parent / manifest['trace_directory']
            checkpoint_directory = manifest_path.parent
        else:
            # Legacy runs remain readable, but entries without response hashes are never trusted.
            identity_path = run_directory / 'request-checkpoints' / 'identity.json'
            if identity_path.is_file() and json.loads(identity_path.read_text()) != self.identity:
                raise ValueError('Checkpoint model/settings changed; cannot reuse timing requests')
            checkpoint_directory = identity_path.parent if identity_path.is_file() else None
            traces = [fallback_trace] if fallback_trace is not None else sorted(run_directory.glob('*.trace'))
            traces = [path for path in traces if path.is_dir()]
            if len(traces) != 1:
                raise ValueError('Resume source needs one timing trace directory or an explicit output path')
            trace = traces[0]
        if trace.is_dir():
            for attempt in sorted(trace.glob('*-attempt.json')):
                meta = json.loads(attempt.read_text())
                if meta.get('status') != 'recorded' or meta.get('finish_reason') != 'STOP':
                    continue
                stem = attempt.name.split('-')[0]
                prompt_path, response_path = trace / (stem + '-prompt.txt'), trace / (stem + '-response.json')
                if not prompt_path.exists() or not response_path.exists():
                    continue
                prompt = prompt_path.read_bytes().decode('utf-8')
                if hashlib.sha256(prompt.encode()).hexdigest() != meta.get('prompt_sha256'):
                    raise ValueError('Timing trace prompt checksum mismatch')
                self._remember(prompt, response_path.read_bytes().decode('utf-8'), meta.get('response_sha256'))
        if checkpoint_directory is not None:
            for checkpoint in sorted(checkpoint_directory.glob('*.json')):
                if checkpoint.name in ('manifest.json', 'identity.json'):
                    continue
                record = json.loads(checkpoint.read_text())
                prompt = record['prompt']
                if checkpoint.stem != hashlib.sha256(prompt.encode()).hexdigest():
                    raise ValueError('Timing checkpoint fingerprint mismatch')
                self._remember(prompt, record['raw'], record.get('response_sha256'))

    def extract(self, prompt):
        self.last_raw_response = None
        self.last_finish_reason = None
        if prompt in self.saved:
            raw = self.saved[prompt]
            self.last_raw_response = raw
            self.last_finish_reason = 'STOP'
            self.reused += 1
            print(f'Timing checkpoint reused ({self.reused})', flush=True)
            value = json.loads(raw)
        else:
            print('Timing request: contacting Gemini', flush=True)
            try:
                value = self._model.extract(prompt)
            finally:
                self.last_raw_response = getattr(self._model, 'last_raw_response', None)
                self.last_finish_reason = getattr(self._model, 'last_finish_reason', None)
        key = hashlib.sha256(prompt.encode()).hexdigest()
        raw = self.last_raw_response
        if isinstance(raw, str) and isinstance(value, dict):
            target = self.directory / (key + '.json')
            temp = target.with_suffix('.tmp')
            temp.write_text(json.dumps({'prompt': prompt, 'raw': raw,
                'response_sha256': hashlib.sha256(raw.encode()).hexdigest()}, ensure_ascii=False))
            temp.replace(target)
        return value
