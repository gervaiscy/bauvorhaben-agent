"""Lossless application view of Gemini's project extraction responses."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .core import Model
from .project_flow import (Document, PageChunk, _document_url, _modality_prompt,
                           _normalize, _obligation_prompt, _permission_verification_prompt,
                           _prompt, make_page_chunks)


def _source_details(document: Document, cited: Any) -> dict[str, Any]:
    """Derive source context without editing or filtering the original citations."""
    ids = {value for value in cited if isinstance(value, str)} if isinstance(cited, list) else set()
    lines = [line for line in document.lines if line.id in ids]
    return {"source_line_ids": [line.id for line in lines],
            "source_pages": sorted({line.page for line in lines}),
            "source_excerpt": "\n".join(line.text for line in lines)}


def _diagnostics(item: Any, kind: str, chunk: PageChunk) -> list[str]:
    if not isinstance(item, dict):
        return ["item_not_object"]
    notes = []
    for field in ("dedupe_key", "name", "actor" if kind == "working_unit" else "provider"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            notes.append(f"missing_or_invalid_{field}")
    citations = item.get("evidence_line_ids")
    if not isinstance(citations, list) or not citations:
        notes.append("missing_or_invalid_evidence_line_ids")
    else:
        visible = {line.id for line in chunk.lines}
        primary = {line.id for line in chunk.lines if line.page in chunk.primary_pages}
        if any(not isinstance(value, str) or value not in visible for value in citations):
            notes.append("unknown_evidence_line_id")
        if not set(value for value in citations if isinstance(value, str)) & primary:
            notes.append("no_primary_page_citation")
    scope = item.get("scope_line_ids", [])
    if not isinstance(scope, list) or any(not isinstance(value, str) or value not in
                                           {line.id for line in chunk.lines} for value in scope):
        notes.append("invalid_scope_line_ids")
    return notes


def _record(document: Document, chunk: PageChunk, kind: str, item: Any,
            response_id: str, position: int) -> dict[str, Any]:
    original = copy.deepcopy(item)
    fields = item if isinstance(item, dict) else {}
    citations = fields.get("evidence_line_ids", [])
    scope = fields.get("scope_line_ids", [])
    combined = (citations if isinstance(citations, list) else []) + (
        scope if isinstance(scope, list) else [])
    details = _source_details(document, combined)
    notes = _diagnostics(item, kind, chunk)
    return {
        "id": f"{response_id}:{kind}:{position:03d}",
        "gemini": original,
        "derived": {"document_id": document.id, "document_title": document.title,
                    "source_document": _document_url(document.source),
                    "response_id": response_id, "response_position": position,
                    **details, "dependency_resolutions": []},
        "validation": {"status": "review_needed" if notes else "valid",
                       "diagnostics": notes},
        "reviews": [],
    }


def _projection(record: dict[str, Any]) -> dict[str, Any] | None:
    """Build a prompt view only; the returned record remains untouched."""
    item = record["gemini"]
    if not isinstance(item, dict):
        return None
    name, actor = item.get("name"), item.get("actor")
    if (not isinstance(name, str) or not name.strip() or
            not isinstance(actor, str) or not actor.strip() or
            not record["derived"]["source_line_ids"]):
        return None
    derived = record["derived"]
    return {"id": record["id"], "name": name, "actor": actor,
            "action": item.get("action"), "due": item.get("due"),
            "document_id": derived["document_id"],
            "source_line_ids": derived["source_line_ids"],
            "source_excerpt": derived["source_excerpt"]}


def _append_response(responses: list[dict[str, Any]], pass_name: str,
                     document: Document | None, chunk: PageChunk | None,
                     response: Any) -> str:
    response_id = f"R{len(responses) + 1:04d}"
    responses.append({"id": response_id, "pass": pass_name,
                      "document_id": document.id if document else None,
                      "primary_pages": list(chunk.primary_pages) if chunk else [],
                      "gemini": copy.deepcopy(response)})
    return response_id


def _annotate_rows(records: list[dict[str, Any]], rows: Any, response_id: str,
                   kind: str, key: str = "unit_id") -> None:
    """Attach reviews by ID, preserving unmatched rows in gemini_responses."""
    if not isinstance(rows, list):
        return
    index = {record["id"]: record for record in records}
    for position, row in enumerate(rows):
        target = index.get(row.get(key)) if isinstance(row, dict) else None
        if target is not None:
            target["reviews"].append({"response_id": response_id, "kind": kind,
                                      "response_position": position,
                                      "gemini": copy.deepcopy(row)})


def _resolve_dependencies(units: list[dict[str, Any]]) -> None:
    by_document: dict[str, dict[str, list[str]]] = {}
    for unit in units:
        item = unit["gemini"]
        key = item.get("dedupe_key") if isinstance(item, dict) else None
        if isinstance(key, str) and key.strip():
            by_document.setdefault(unit["derived"]["document_id"], {}).setdefault(
                _normalize(key), []).append(unit["id"])
    for unit in units:
        item = unit["gemini"]
        if not isinstance(item, dict):
            continue
        index = by_document.get(unit["derived"]["document_id"], {})
        for field in ("depends_on", "scoped_dependencies"):
            original = item.get(field, [])
            if not isinstance(original, list):
                unit["validation"]["diagnostics"].append(f"invalid_{field}")
                unit["validation"]["status"] = "review_needed"
                continue
            for position, dependency in enumerate(original):
                key = (dependency.get("dedupe_key") if isinstance(dependency, dict)
                       else dependency) if field == "scoped_dependencies" else dependency
                candidates = index.get(_normalize(key), []) if isinstance(key, str) else []
                unit["derived"]["dependency_resolutions"].append({
                    "field": field, "position": position, "original_key": copy.deepcopy(key),
                    "status": "resolved" if len(candidates) == 1 else (
                        "ambiguous" if candidates else "unresolved"),
                    "candidate_unit_ids": candidates})


def extract_project_units(documents: list[Document], model: Model,
                          whole_document_max_chars: int = 80000, chunk_pages: int = 3,
                          overlap_pages: int = 1, language: str = "de") -> dict[str, Any]:
    """Retain every proposal and every later Gemini result without semantic edits."""
    if language not in ("de", "en"):
        raise ValueError(f"Unsupported output language: {language}")
    units: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    for document in documents:
        prior_context: dict[str, Any] = {}
        for chunk in make_page_chunks(document, whole_document_max_chars,
                                      chunk_pages, overlap_pages):
            known_items = []
            for kind, records in (("working_unit", units), ("offer", offers)):
                for record in records:
                    item = record["gemini"]
                    if record["derived"]["document_id"] != document.id or not isinstance(item, dict):
                        continue
                    if isinstance(item.get("dedupe_key"), str) and isinstance(item.get("name"), str):
                        known_items.append({"dedupe_key": item["dedupe_key"],
                                            "name": item["name"],
                                            "actor": item.get("actor" if kind == "working_unit" else "provider"),
                                            "kind": kind})
            response = model.extract(_prompt(document, chunk, prior_context, known_items, language))
            response_id = _append_response(responses, "extraction", document, chunk, response)
            if isinstance(response, dict):
                for field, kind, target in (("working_units", "working_unit", units),
                                            ("offers", "offer", offers)):
                    items = response.get(field)
                    if isinstance(items, list):
                        for position, item in enumerate(items):
                            target.append(_record(document, chunk, kind, item,
                                                  response_id, position))
                carry = response.get("carry_forward")
                prior_context = carry if isinstance(carry, dict) else {}
    _resolve_dependencies(units)

    projected = [view for unit in units if (view := _projection(unit)) is not None]
    proposed_permissions: list[tuple[Document, dict[str, Any], dict[str, Any]]] = []
    for document in documents:
        for chunk in make_page_chunks(document, whole_document_max_chars,
                                      chunk_pages, overlap_pages):
            primary = {line.id for line in chunk.lines if line.page in chunk.primary_pages}
            candidates = [unit for unit in projected if unit["document_id"] == document.id
                          and set(unit["source_line_ids"]) & primary]
            if not candidates:
                continue
            response = model.extract(_modality_prompt(document, chunk, candidates, language))
            response_id = _append_response(responses, "modality", document, chunk, response)
            rows = response.get("classifications") if isinstance(response, dict) else None
            _annotate_rows(units, rows, response_id, "modality")
            if isinstance(rows, list):
                by_id = {unit["id"]: unit for unit in candidates}
                for row in rows:
                    if not isinstance(row, dict) or row.get("modality") != "PERMISSION":
                        continue
                    unit = by_id.get(row.get("unit_id"))
                    if unit is not None:
                        proposed_permissions.append((document, unit, row))
    if proposed_permissions:
        response = model.extract(_permission_verification_prompt(proposed_permissions, language))
        response_id = _append_response(responses, "permission_verification", None, None, response)
        rows = response.get("verifications") if isinstance(response, dict) else None
        _annotate_rows(units, rows, response_id, "permission_verification")

    for document in documents:
        for chunk in make_page_chunks(document, whole_document_max_chars,
                                      chunk_pages, overlap_pages):
            response = model.extract(_obligation_prompt(document, chunk, projected, language))
            response_id = _append_response(responses, "relationships", document, chunk, response)
            rows = response.get("related_obligations") if isinstance(response, dict) else None
            _annotate_rows(units, rows, response_id, "related_obligation", "target_unit_id")
    snapshots = []
    for document in documents:
        transcript = [{'id': line.id, 'pdf_page': line.page, 'text': line.text} for line in document.lines]
        snapshots.append({'document_id': document.id, 'line_ids': [line.id for line in document.lines],
                          'sha256': hashlib.sha256(json.dumps(transcript, sort_keys=True, ensure_ascii=False,
                                                              separators=(',', ':')).encode()).hexdigest()})
    return {"schema_version": 9, "source_snapshots": snapshots, "offers": offers, "working_units": units,
            "gemini_responses": responses}
