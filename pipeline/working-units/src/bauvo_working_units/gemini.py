"""Small Google Gen AI / Vertex AI adapter."""

from __future__ import annotations

import json
import time
from typing import Any


class GeminiModel:
    def __init__(self, project: str, location: str, model: str):
        from google import genai
        from google.genai import types

        self._client = genai.Client(vertexai=True, project=project, location=location,
                                    http_options=types.HttpOptions(api_version="v1", timeout=600_000))
        self._types = types
        self._model = model
        self.last_raw_response: str | None = None

    def extract(self, prompt: str) -> Any:
        self.last_raw_response = None
        for attempt in range(5):
            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=prompt,
                    config=self._types.GenerateContentConfig(
                        temperature=0,
                        response_mime_type="application/json",
                    ),
                )
                break
            except Exception as exc:
                if getattr(exc,"code",None) not in (429,500,502,503,504) or attempt==4:raise
                delay=min(60,15*(2**attempt))
                print(f"Vertex capacity/server limit; retrying in {delay}s (attempt {attempt+2}/5)",flush=True)
                time.sleep(delay)
        self.last_raw_response = response.text
        candidates=getattr(response,"candidates",None)
        if candidates and str(candidates[0].finish_reason).split(".")[-1]!="STOP":
            raise ValueError("Incomplete working-unit response: "+str(candidates[0].finish_reason))
        if not response.text:
            return None
        try:
            return json.loads(response.text)
        except json.JSONDecodeError:
            return response.text
