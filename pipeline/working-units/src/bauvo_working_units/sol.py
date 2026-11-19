"""OpenAI Responses adapter; exactly one provider attempt, with source JSON preserved."""
import copy
import json

from .gemini import IncompleteResponse
from .schemas import RESPONSE_SCHEMAS, generation_for, response_schema_for

DEFAULT_MODEL = 'gpt-6.1-sol'
DEFAULT_REASONING = 'medium'


def generation_for_sol(phase, reasoning=DEFAULT_REASONING):
    return {'reasoning': {'effort': reasoning},
            'max_output_tokens': generation_for(phase)['max_output_tokens'],
            'service_tier': 'default', 'store': False, 'tools': []}


def request_for(prompt, model=DEFAULT_MODEL, reasoning=DEFAULT_REASONING):
    phase = prompt.split('.', 1)[0]
    if phase not in RESPONSE_SCHEMAS:
        raise ValueError('No structured response schema for Sol phase')
    return {'model': model, 'input': prompt, **generation_for_sol(phase, reasoning),
            'text': {'format': {'type': 'json_schema', 'name': phase.lower(), 'strict': True,
                               'schema': response_schema_for(prompt)}}}


class SolProviderError(RuntimeError):
    """Safe provider failure metadata, without credential or response headers."""
    def __init__(self, code=None, retryable=False):
        self.code = code
        self.retryable = retryable
        self.reason = 'transport' if retryable else 'provider_error'
        super().__init__('OpenAI request failed; see preserved provider diagnostics')


class SolModel:
    def __init__(self, model=DEFAULT_MODEL, reasoning=DEFAULT_REASONING, client=None):
        if client is None:
            from openai import OpenAI
            client = OpenAI(base_url='https://api.openai.com/v1', max_retries=0, timeout=600)
        self.client, self.model, self.reasoning = client, model, reasoning
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None

    def extract(self, prompt):
        self.last_raw_response = self.last_finish_reason = self.last_diagnostics = None
        body = request_for(prompt, self.model, self.reasoning)
        self.last_diagnostics = {'provider': 'openai', 'requested_model': self.model,
                                 'generation_config': generation_for_sol(prompt.split('.', 1)[0], self.reasoning),
                                 'usage_metadata': None}
        print(f'Sol request: model={self.model}; max_output_tokens={body["max_output_tokens"]}; '
              f'reasoning={self.reasoning}', flush=True)
        try:
            response = self.client.responses.create(**body)
        except Exception as exc:
            from openai import APIConnectionError
            self.last_diagnostics['provider_error'] = {'type': type(exc).__name__,
                                                       'status_code': getattr(exc, 'status_code', None)}
            raise SolProviderError(getattr(exc, 'status_code', None),
                                   isinstance(exc, APIConnectionError)) from None
        raw = response.model_dump(mode='json')
        usage = raw.get('usage') or {}
        self.last_diagnostics.update(provider_response=copy.deepcopy(raw),
            model_version=raw.get('model'), response_id=raw.get('id'),
            usage_metadata={'provider': 'openai', **copy.deepcopy(usage)})
        self.last_raw_response = ''.join(part['text'] for item in raw.get('output', [])
            if item.get('type') == 'message' for part in item.get('content', [])
            if part.get('type') == 'output_text')
        refusal = any(part.get('type') == 'refusal' for item in raw.get('output', [])
                      if item.get('type') == 'message' for part in item.get('content', []))
        reason = (raw.get('incomplete_details') or {}).get('reason')
        self.last_finish_reason = ('SAFETY' if refusal else 'STOP' if raw.get('status') == 'completed'
                                  else 'MAX_TOKENS' if reason == 'max_output_tokens'
                                  else str(reason or raw.get('status') or 'UNKNOWN').upper())
        print(f'Sol response: model={raw.get("model")}; finish={self.last_finish_reason}; '
              f'input_tokens={usage.get("input_tokens")}; output_tokens={usage.get("output_tokens")}; '
              f'reasoning_tokens={(usage.get("output_tokens_details") or {}).get("reasoning_tokens")}', flush=True)
        if self.last_finish_reason != 'STOP':
            raise IncompleteResponse('unfinished_generation', self.last_finish_reason)
        if not self.last_raw_response:
            raise IncompleteResponse('empty_response', self.last_finish_reason)
        try:
            return json.loads(self.last_raw_response)
        except ValueError:
            raise IncompleteResponse('invalid_json', self.last_finish_reason) from None
