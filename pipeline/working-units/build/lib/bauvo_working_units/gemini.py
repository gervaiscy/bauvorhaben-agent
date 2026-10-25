"""Small Google Gen AI / Vertex AI adapter; one provider request per call."""
from __future__ import annotations

import json
from typing import Any
from .schemas import GENERATION, RESPONSE_SCHEMAS


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

    def extract(self, prompt: str) -> Any:
        self.last_raw_response = None
        self.last_finish_reason = None
        response = self._client.models.generate_content(
            model=self._model, contents=prompt,
            config=self._types.GenerateContentConfig(**GENERATION,
                **({'response_json_schema': RESPONSE_SCHEMAS[prompt.split('.', 1)[0]]}
                   if prompt.split('.', 1)[0] in RESPONSE_SCHEMAS else {})))
        self.last_raw_response = response.text
        candidates = getattr(response, 'candidates', None)
        if candidates:
            reason = getattr(candidates[0], 'finish_reason', None)
            self.last_finish_reason = str(reason).split('.')[-1] if reason is not None else None
        if self.last_finish_reason not in (None, 'STOP'):
            raise IncompleteResponse('unfinished_generation', self.last_finish_reason)
        if not response.text:
            raise IncompleteResponse('empty_response', self.last_finish_reason)
        try:
            return json.loads(response.text)
        except json.JSONDecodeError as exc:
            raise IncompleteResponse('invalid_json', self.last_finish_reason) from exc
