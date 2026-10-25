"""Preserve raw provider attempts separately from application interpretations."""
from __future__ import annotations

import hashlib
import json
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
        except Exception as exc:
            metadata.update(status='request_failed', **failure_details(exc))
            raise
        finally:
            raw = getattr(self._model, 'last_raw_response', None)
            self.last_raw_response = raw if isinstance(raw, str) else None
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
