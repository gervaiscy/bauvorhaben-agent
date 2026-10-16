"""Record Gemini calls without changing their responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import Model


class TracingModel:
    def __init__(self, model: Model, directory: str | Path):
        self._model = model
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        if list(self._directory.glob("*-prompt.txt")) or list(self._directory.glob("*-response.json")):
            raise FileExistsError(f"Trace directory already has model calls: {self._directory}")
        self._call_number = 0

    def extract(self, prompt: str) -> Any:
        self._call_number += 1
        stem = f"{self._call_number:04d}"
        (self._directory / f"{stem}-prompt.txt").write_text(prompt, encoding="utf-8")
        response = self._model.extract(prompt)
        raw = getattr(self._model, "last_raw_response", None)
        (self._directory / f"{stem}-response.json").write_text(
            raw if isinstance(raw, str) else json.dumps(response, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        return response


class RawRunRecorder:
    """Keep Gemini's returned JSON values in one file without pipeline filtering."""

    def __init__(self, model: Model, path: str | Path):
        self._model = model
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._responses: list[Any] = []
        self.last_raw_response: str | None = None
        self._write()

    def _write(self) -> None:
        self._path.write_text(json.dumps(self._responses, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")

    def extract(self, prompt: str) -> Any:
        self.last_raw_response = None
        try:
            response = self._model.extract(prompt)
            raw = getattr(self._model, "last_raw_response", None)
            self.last_raw_response = raw if isinstance(raw, str) else None
            if isinstance(raw, str):
                try:
                    saved = json.loads(raw)
                except ValueError:
                    saved = raw
            else:
                saved = json.loads(json.dumps(response, ensure_ascii=False))
            self._responses.append(saved)
            return response
        except Exception:
            raw = getattr(self._model, "last_raw_response", None)
            self.last_raw_response = raw if isinstance(raw, str) else None
            if self.last_raw_response is not None:
                try:
                    value = json.loads(self.last_raw_response)
                except ValueError:
                    value = self.last_raw_response
                self._responses.append(value)
            raise
        finally:
            self._write()
