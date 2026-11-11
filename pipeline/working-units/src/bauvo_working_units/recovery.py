"""Hash-verified request recovery; original attempts are never modified."""
import copy
import hashlib
import json
from pathlib import Path
import re

from .core import digest
from .schemas import RESPONSE_SCHEMAS, generation_for

POLICY = Path(__file__).with_name('recovery-bindings.json')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def source_files(root):
    return sorted(p for folder in (Path(__file__).parent, root/'pipeline/pdf-extractor',
                                   root/'pipeline/document-boundaries')
                  for p in folder.rglob('*') if p.suffix in ('.py', '.txt'))


def compatible_code(root):
    """Only the generation ceiling may differ; all other code/prompts must match."""
    files = {}
    for path in source_files(root):
        data = path.read_bytes()
        if path == Path(__file__).with_name('schemas.py'):
            data = re.sub(rb"('max_output_tokens': )\d+", rb'\1GENERATION_LIMIT', data)
        files[str(path.relative_to(root))] = hashlib.sha256(data).hexdigest()
    return digest(files)


def generation_settings():
    return {name: generation_for(name) for name in RESPONSE_SCHEMAS}


def budget_compatible(previous, current):
    if not isinstance(previous, dict) or set(previous) != set(current):
        return False
    for name, now in current.items():
        old = previous[name]
        if not isinstance(old, dict):
            return False
        before, after = old.get('max_output_tokens'), now.get('max_output_tokens')
        if type(before) is not int or type(after) is not int or before > after:
            return False
        if {k:v for k,v in old.items() if k != 'max_output_tokens'} != {
                k:v for k,v in now.items() if k != 'max_output_tokens'}:
            return False
    return True


def resume_compatibility(previous, current):
    """Return old generation settings only for an exact, versioned migration."""
    excluded = {'code', 'recovery'}
    if not isinstance(previous, dict) or {k:v for k,v in previous.items() if k not in excluded} != {
            k:v for k,v in current.items() if k not in excluded}:
        return None
    old = previous.get('recovery')
    now = current['recovery']
    if old is not None:
        if (old.get('code') == now['code'] and old.get('generation') != now['generation']
                and budget_compatible(old.get('generation'), now['generation'])):
            return old['generation']
        return None
    # Older runs stored one opaque code hash. Accept only the two known releases
    # before this recovery feature, and only under its unchanged migration target.
    policy = read(POLICY)
    ceiling = policy['legacy_code_limits'].get(previous.get('code'))
    if ceiling is None or now['code'] != policy['target_compatible_code']:
        return None
    settings = copy.deepcopy(now['generation'])
    for config in settings.values():
        config['max_output_tokens'] = ceiling
    return settings if budget_compatible(settings, now['generation']) else None


class RequestRecovery:
    """Replay exact prompts, preserving original generation settings as provenance."""
    def __init__(self, model, folder, previous_folders, identity, legacy_generation=None):
        self.model = model
        self.folder = Path(folder)
        self.identity = copy.deepcopy(identity)
        self.generation = generation_settings()
        self.saved = {}
        self.last_raw_response = None
        self.last_finish_reason = None
        self.last_diagnostics = None
        self.last_recovery = None
        self.reused = 0
        for previous in previous_folders:
            self._load(Path(previous), legacy_generation)
        settings = {'identity': self.identity, 'generation': self.generation}
        write(self.folder/'request-settings.json', {**settings, 'settings_sha256':digest(settings)})

    def _remember(self, prompt, raw, settings, origin, diagnostics=None):
        if not isinstance(raw, str):
            raise ValueError('Recovery response is not text')
        value = json.loads(raw)
        name = prompt.split('.', 1)[0]
        if name not in self.generation or not budget_compatible({name: settings}, {name:self.generation[name]}):
            return
        self.saved[prompt] = (raw, copy.deepcopy(settings), str(origin), copy.deepcopy(diagnostics))

    def _load(self, folder, legacy_generation):
        settings_path = folder/'request-settings.json'
        if settings_path.exists():
            settings = read(settings_path)
            if settings.get('settings_sha256') != digest({k:v for k,v in settings.items()
                                                          if k != 'settings_sha256'}):
                raise ValueError('Recovery settings checksum mismatch')
            if settings.get('identity') != self.identity:
                raise ValueError('Recovery model/project settings changed')
            generation = settings['generation']
        else:
            generation = legacy_generation
        if generation is None:
            return
        for path in sorted((folder/'requests').glob('*.json')):
            record = read(path)
            request = record['request']
            if digest(request) != path.stem or record['request_sha256'] != path.stem:
                raise ValueError('Recovery request checksum mismatch')
            raw = record['response']
            if hashlib.sha256(raw.encode()).hexdigest() != record['response_sha256']:
                raise ValueError('Recovery response checksum mismatch')
            if request['identity'] != self.identity:
                raise ValueError('Recovery model/project settings changed')
            if 'diagnostics' in record and record.get('diagnostics_sha256') != digest(record['diagnostics']):
                raise ValueError('Recovery diagnostics checksum mismatch')
            if 'response_schema' not in request or request['response_schema'] == RESPONSE_SCHEMAS.get(request['prompt'].split('.', 1)[0]):
                self._remember(request['prompt'], raw, request['generation'], path, record.get('diagnostics'))
        # Import historical traces too, including runs interrupted before result.json.
        for path in sorted((folder/'trace').glob('*-attempt.json')):
            try:
                metadata = read(path)
            except json.JSONDecodeError:
                # A process can stop during the older, non-atomic trace write.
                # Keep that file; independently verified atomic checkpoints can recover it.
                continue
            if metadata.get('status') != 'recorded' or metadata.get('finish_reason') != 'STOP':
                continue
            stem = path.name.split('-')[0]
            prompt_path = path.with_name(stem+'-prompt.txt')
            response_path = path.with_name(stem+'-response.json')
            if not prompt_path.is_file() or not response_path.is_file():
                continue
            prompt = prompt_path.read_bytes().decode('utf-8')
            raw = response_path.read_bytes().decode('utf-8')
            if hashlib.sha256(prompt.encode()).hexdigest() != metadata.get('prompt_sha256'):
                raise ValueError('Recovery prompt checksum mismatch')
            checksum = metadata.get('response_sha256')
            if checksum is None:
                continue  # Unverified legacy evidence remains untouched, never replayed.
            if hashlib.sha256(raw.encode()).hexdigest() != checksum:
                raise ValueError('Recovery response checksum mismatch')
            name = prompt.split('.', 1)[0]
            diagnostics = metadata.get('diagnostics')
            schema = (diagnostics or {}).get('generation_config', {}).get('response_json_schema')
            if schema is not None and schema != RESPONSE_SCHEMAS.get(name):
                continue
            original = metadata.get('recovery', {}).get('generation', generation.get(name))
            if original is not None:
                self._remember(prompt, raw, original, response_path, metadata.get('diagnostics'))

    def prepare(self, prompts):
        missing = [prompt for prompt in prompts if prompt not in self.saved]
        print(f'Recovery plan: {len(prompts)-len(missing)} saved; {len(missing)} requests need provider-store lookup', flush=True)
        from .call_budget import prepare_requests
        return prepare_requests(self.model, missing)

    def extract(self, prompt):
        self.last_raw_response = None
        self.last_finish_reason = None
        self.last_diagnostics = None
        self.last_recovery = None
        name = prompt.split('.', 1)[0]
        settings = self.generation[name]
        saved = self.saved.get(prompt)
        if saved is not None:
            raw, original, origin, diagnostics = saved
            self.last_raw_response = raw
            self.last_finish_reason = 'STOP'
            self.last_recovery = {'response_file': origin, 'generation': original}
            self.last_diagnostics = copy.deepcopy(diagnostics)
            self.reused += 1
            print(f'Analysis checkpoint reused ({self.reused})', flush=True)
            value = json.loads(raw)
            settings = original
        else:
            try:
                value = self.model.extract(prompt)
            finally:
                self.last_raw_response = getattr(self.model, 'last_raw_response', None)
                self.last_finish_reason = getattr(self.model, 'last_finish_reason', None)
                self.last_diagnostics = copy.deepcopy(getattr(self.model, 'last_diagnostics', None))
        if self.last_finish_reason == 'STOP' and isinstance(self.last_raw_response, str):
            request = {'prompt':prompt, 'identity':self.identity, 'generation':settings,
                       'response_schema': copy.deepcopy(RESPONSE_SCHEMAS.get(name))}
            key = digest(request)
            record = {
                'request':request, 'request_sha256':key, 'response':self.last_raw_response,
                'response_sha256':hashlib.sha256(self.last_raw_response.encode()).hexdigest(),
                'recovery':self.last_recovery}
            if self.last_diagnostics is not None:
                record.update(diagnostics=self.last_diagnostics,diagnostics_sha256=digest(self.last_diagnostics))
            path = self.folder/'requests'/(key+'.json')
            if path.exists():
                prior = read(path)
                if prior.get('request') != request or prior.get('response_sha256') != record['response_sha256']:
                    raise ValueError('Recovery response already exists; original retained')
            else:
                write(path, record)
        return value
