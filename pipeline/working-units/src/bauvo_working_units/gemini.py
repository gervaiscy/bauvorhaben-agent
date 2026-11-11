"""Small Google Gen AI / Vertex AI adapter; one provider request per call."""
from __future__ import annotations

import json
import copy
from typing import Any
from .schemas import generation_for, RESPONSE_SCHEMAS


class IncompleteResponse(ValueError):
    """Generation was unfinished, empty or not parseable JSON; never a semantic repair."""
    def __init__(self, reason, finish_reason=None):
        self.reason = reason
        self.finish_reason = finish_reason
        super().__init__(f'{reason}; finish_reason={finish_reason}')


class GeminiModel:
    def __init__(self, project: str, location: str, model: str):
        from google import genai
        from google.genai import types

        self._client = genai.Client(vertexai=True, project=project, location=location,
                                    http_options=types.HttpOptions(api_version="v1", timeout=180_000,
                                        retry_options=types.HttpRetryOptions(attempts=1)))
        self._types = types
        self._model = model
        self.last_raw_response: str | None = None
        self.last_finish_reason: str | None = None
        self.last_diagnostics: dict | None = None

    def extract(self, prompt: str) -> Any:
        self.last_raw_response = None
        self.last_finish_reason = None
        self.last_diagnostics = None
        phase = prompt.split('.', 1)[0]
        config = self._types.GenerateContentConfig(**generation_for(phase),
            **({'response_json_schema': RESPONSE_SCHEMAS[phase]} if phase in RESPONSE_SCHEMAS else {}))
        settings = (config.model_dump(mode='json', exclude_none=True)
                    if hasattr(config, 'model_dump') else copy.deepcopy(config))
        self.last_diagnostics = {'requested_model': self._model, 'generation_config': settings,
                                 'model_version': None, 'response_id': None, 'usage_metadata': None}
        print(f"Gemini request: model={self._model}; max_output_tokens={settings.get('max_output_tokens')}; "
              f"thinking={settings.get('thinking_config', {}).get('thinking_level')}", flush=True)
        response = self._client.models.generate_content(
            model=self._model, contents=prompt,
            config=config)
        usage = getattr(response, 'usage_metadata', None)
        counts = {}
        for field in ('prompt_token_count', 'candidates_token_count', 'thoughts_token_count',
                      'total_token_count', 'cached_content_token_count', 'tool_use_prompt_token_count'):
            value = usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)
            if value is not None:
                counts[field] = value
        self.last_diagnostics.update(model_version=getattr(response, 'model_version', None),
            response_id=getattr(response, 'response_id', None), usage_metadata=counts if usage is not None else None)
        candidates = getattr(response, 'candidates', None)
        if candidates:
            reason = getattr(candidates[0], 'finish_reason', None)
            self.last_finish_reason = str(reason).split('.')[-1] if reason is not None else None
        print(f"Gemini response: model={self.last_diagnostics['model_version']}; finish={self.last_finish_reason}; "
              f"input_tokens={counts.get('prompt_token_count')}; output_tokens={counts.get('candidates_token_count')}; "
              f"thinking_tokens={counts.get('thoughts_token_count')}; total_tokens={counts.get('total_token_count')}", flush=True)
        self.last_raw_response = response.text
        if self.last_finish_reason not in (None, 'STOP'):
            raise IncompleteResponse('unfinished_generation', self.last_finish_reason)
        if not response.text:
            raise IncompleteResponse('empty_response', self.last_finish_reason)
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise IncompleteResponse('invalid_json', self.last_finish_reason) from exc
