"""Immutable completed provider responses, independent of application validation."""
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from .schemas import generation_for, RESPONSE_SCHEMAS


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def provider_request(prompt, identity):
    # Only settings that actually reach this request belong in its identity.
    phase = prompt.split('.', 1)[0]
    from .schemas import response_schema_for
    if identity.get('provider') == 'sol':
        from .sol import generation_for_sol
        return {'identity': {'provider': 'sol', 'model': identity['model'],
                             'reasoning': identity.get('reasoning', 'medium')},
                'prompt': prompt, 'generation': generation_for_sol(phase, identity.get('reasoning', 'medium')),
                'response_schema': response_schema_for(prompt)}
    return {'identity': {k: copy.deepcopy(identity[k]) for k in ('project', 'location', 'model') if k in identity},
            'prompt': prompt, 'generation': generation_for(phase),
            'response_schema': response_schema_for(prompt)}


class CachedModel:
    def __init__(self, model, directory, identity, budget=None, plan_path=None, refresh_requests=(), plan_only=False):
        self.model, self.directory, self.identity = model, Path(directory), identity
        self.budget = budget if budget is not None else getattr(model, 'budget', None)
        self.plan_path = Path(plan_path) if plan_path else None
        self.plans = []
        self.plan_only = plan_only
        self.refresh_requests = set(refresh_requests)
        if any(len(key)!=64 or any(c not in "0123456789abcdef" for c in key) for key in self.refresh_requests):
            raise ValueError("Refresh request IDs must be SHA-256 values from the request plan")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None

    def _load(self, prompt):
        request = provider_request(prompt, self.identity)
        key = digest(request)
        path = self.directory / (key + '.json')
        head = self.directory / 'heads' / (key + '.json')
        if head.exists():
            selected = json.loads(head.read_text())
            checksum = selected.get('response_sha256')
            if not isinstance(checksum, str) or len(checksum)!=64 or any(c not in '0123456789abcdef' for c in checksum):
                raise ValueError('Invalid selected provider response')
            path = self.directory / 'versions' / key / (checksum + '.json')
            if not path.is_file():
                raise ValueError('Selected provider response missing')
        if not path.exists():
            return request, path, None
        saved = json.loads(path.read_text())
        if not {'request','request_sha256','response','response_sha256','raw_text','finish_reason'} <= saved.keys():
            raise ValueError('Incomplete provider response record')
        if (saved.get('request') != request or saved.get('request_sha256') != key
                or saved.get('response_sha256') != digest({k: saved.get(k) for k in ('response', 'raw_text', 'finish_reason')})):
            raise ValueError('Working-unit model cache modified')
        if head.exists() and saved.get('response_sha256') != checksum:
            raise ValueError('Selected provider response checksum mismatch')
        if 'diagnostics' in saved and saved.get('diagnostics_sha256') != digest(saved['diagnostics']):
            raise ValueError('Working-unit cache diagnostics modified')
        if saved.get('finish_reason') not in (None, 'STOP'):
            raise ValueError('Cache record is not a completed JSON provider response')
        if saved.get('raw_text') is not None and json.loads(saved['raw_text']) != saved['response']:
            raise ValueError('Cache raw response differs from parsed response')
        return request, path, saved

    def prepare(self, prompts):
        items = []
        for prompt in dict.fromkeys(prompts):
            request, path, saved = self._load(prompt)
            if digest(request) in self.refresh_requests:
                if saved is None:
                    raise ValueError('Cannot refresh an unknown request; use a saved request SHA from the plan')
                saved = None
            items.append({'request_sha256': digest(request), 'phase': prompt.split('.', 1)[0],
                          'source': 'saved_response' if saved else 'new_call', 'response_file': str(path)})
        plan = {'requests': items, 'saved_responses': sum(i['source']=='saved_response' for i in items),
                'planned_new_calls': sum(i['source']=='new_call' for i in items),
                'call_limit': self.budget.limit if self.budget else None,
                'calls_used': self.budget.used if self.budget else 0,
                'retry_policy': 'at most one retry for transport/truncation; every attempt consumes the same limit',
                'scope': 'known requests; later dependent stages are planned when their inputs exist'}
        self.plans.append(plan)
        if self.plan_path:
            self.plan_path.parent.mkdir(parents=True, exist_ok=True)
            self.plan_path.write_text(json.dumps(self.plans, indent=2)+'\n')
        print(f"Request plan: {len(items)} requests; {plan['saved_responses']} saved; "
              f"{plan['planned_new_calls']} new; remaining limit={self.budget.remaining if self.budget else 'unlimited'}", flush=True)
        if self.plan_only and plan['planned_new_calls']:
            from .call_budget import PlanOnlyStop
            raise PlanOnlyStop(f"Planned {plan['planned_new_calls']} new provider calls; no calls permitted in plan-only mode")
        if self.budget:
            self.budget.check(plan['planned_new_calls'])
        return plan

    def extract(self, prompt):
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None
        request, path, saved = self._load(prompt)
        refreshing = digest(request) in self.refresh_requests
        if saved is not None and not refreshing:
            print('Working-unit request: saved provider response replayed', flush=True)
            self.last_raw_response, self.last_finish_reason = saved.get('raw_text'), saved.get('finish_reason')
            self.last_diagnostics = copy.deepcopy(saved.get('diagnostics'))
            return copy.deepcopy(saved['response'])
        # Report each dynamically determined request, too. A full stage preflight
        # may have already reported it; the budget counts attempts only at the backend.
        self.prepare([prompt])
        try:
            answer = self.model.extract(prompt)
        finally:
            self.last_raw_response = getattr(self.model, 'last_raw_response', None)
            self.last_finish_reason = getattr(self.model, 'last_finish_reason', None)
            self.last_diagnostics = copy.deepcopy(getattr(self.model, 'last_diagnostics', None))
        if self.last_finish_reason in (None, 'STOP'):
            saved = {'request_sha256': digest(request), 'request': request, 'response': copy.deepcopy(answer),
                     'raw_text': self.last_raw_response, 'finish_reason': self.last_finish_reason}
            saved['response_sha256'] = digest({k: saved[k] for k in ('response', 'raw_text', 'finish_reason')})
            if self.last_raw_response is not None and json.loads(self.last_raw_response) != answer:
                raise ValueError('Provider raw response differs from parsed response')
            if self.last_diagnostics is not None:
                saved.update(diagnostics=self.last_diagnostics, diagnostics_sha256=digest(self.last_diagnostics))
            if refreshing:
                path = self.directory / 'versions' / digest(request) / (saved['response_sha256']+'.json')
            self._store(path, saved)
            if refreshing:
                head = self.directory / 'heads' / (digest(request)+'.json')
                head.parent.mkdir(parents=True, exist_ok=True)
                temporary = head.with_suffix('.tmp')
                temporary.write_text(json.dumps({'response_sha256': saved['response_sha256']}))
                temporary.replace(head)
                self.refresh_requests.remove(digest(request))
            print('Completed provider response saved; local validation runs separately', flush=True)
        return answer

    def _store(self, path, saved):
        """Atomic publication without replacing an existing immutable response."""
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.response-')
        try:
            with os.fdopen(descriptor, 'w') as handle:
                json.dump(saved, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                if json.loads(path.read_text()) != saved:
                    raise ValueError('Concurrent provider responses differ; original record preserved')
        finally:
            os.unlink(temporary)
