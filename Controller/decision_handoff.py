"""Bounded structured continuity between successful top-level decisions."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any


HANDOFF_SCHEMA_VERSION = 1
MAX_ASSESSMENT_CHARS = 600
MAX_OPEN_LOOPS = 6
MAX_OBJECTIVE_CHARS = 180
MAX_NEXT_ACTION_CHARS = 220
MAX_REASON_CHARS = 220
MAX_HANDOFF_CHARS = 4_000
LOOP_STATUSES = {"pending", "blocked", "deferred"}
LOOP_RESOLUTIONS = {"completed", "cancelled", "invalidated"}
LOOP_ID_PATTERN = re.compile(r"^L-[0-9a-f]{6}(?:-[1-9][0-9]?)?$")


class DecisionHandoffError(ValueError):
    pass


def prepare_handoff(arguments: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise DecisionHandoffError("finish_decision arguments must be an object")
    assessment = bounded_text(arguments.get("assessment"), "assessment", MAX_ASSESSMENT_CHARS, required=False)
    open_values = arguments.get("open_loops")
    resolution_values = arguments.get("resolved_loops")
    if not isinstance(open_values, list) or not isinstance(resolution_values, list):
        raise DecisionHandoffError("open_loops and resolved_loops must be arrays")
    if len(open_values) > MAX_OPEN_LOOPS or len(resolution_values) > MAX_OPEN_LOOPS:
        raise DecisionHandoffError(f"at most {MAX_OPEN_LOOPS} open loops and resolutions are allowed")

    prior = validate_handoff(previous) if previous is not None else empty_handoff()
    prior_by_id = {item["id"]: item for item in prior["openLoops"]}
    prior_by_objective = {normalize_text(item["objective"]): item["id"] for item in prior["openLoops"]}
    retained: set[str] = set()
    loops: list[dict[str, str]] = []
    used_ids: set[str] = set()

    for raw in open_values:
        if not isinstance(raw, dict):
            raise DecisionHandoffError("each open loop must be an object")
        objective = bounded_text(raw.get("objective"), "objective", MAX_OBJECTIVE_CHARS)
        next_action = bounded_text(raw.get("next_action"), "next_action", MAX_NEXT_ACTION_CHARS)
        status = str(raw.get("status") or "")
        if status not in LOOP_STATUSES:
            raise DecisionHandoffError("open-loop status must be pending, blocked, or deferred")
        reason = bounded_text(raw.get("reason"), "reason", MAX_REASON_CHARS, required=False)
        requested_id = raw.get("id")
        if requested_id is not None and not isinstance(requested_id, str):
            raise DecisionHandoffError("open-loop id must be a string or null")
        if requested_id:
            if requested_id not in prior_by_id:
                raise DecisionHandoffError(f"unknown prior open-loop id: {requested_id}")
            loop_id = requested_id
        else:
            loop_id = prior_by_objective.get(normalize_text(objective)) or make_loop_id(objective, used_ids | set(prior_by_id))
        if loop_id in used_ids:
            raise DecisionHandoffError(f"duplicate open-loop id: {loop_id}")
        if loop_id in prior_by_id:
            retained.add(loop_id)
        used_ids.add(loop_id)
        loops.append({
            "id": loop_id,
            "objective": objective,
            "nextAction": next_action,
            "status": status,
            "reason": reason,
        })

    resolved: set[str] = set()
    for raw in resolution_values:
        if not isinstance(raw, dict):
            raise DecisionHandoffError("each resolution must be an object")
        loop_id = str(raw.get("id") or "")
        resolution = str(raw.get("resolution") or "")
        if loop_id not in prior_by_id:
            raise DecisionHandoffError(f"unknown prior open-loop id: {loop_id}")
        if loop_id in retained or loop_id in resolved:
            raise DecisionHandoffError(f"open loop accounted for more than once: {loop_id}")
        if resolution not in LOOP_RESOLUTIONS:
            raise DecisionHandoffError("resolution must be completed, cancelled, or invalidated")
        bounded_text(raw.get("reason"), "resolution reason", MAX_REASON_CHARS, required=False)
        resolved.add(loop_id)

    missing = sorted(set(prior_by_id) - retained - resolved)
    if missing:
        raise DecisionHandoffError("prior open loops must be retained or resolved: " + ", ".join(missing))
    handoff = {"schemaVersion": HANDOFF_SCHEMA_VERSION, "assessment": assessment, "openLoops": loops}
    return validate_handoff(handoff)


def fallback_handoff(previous: dict[str, Any] | None, assessment: str) -> dict[str, Any]:
    prior = validate_handoff(previous) if previous is not None else empty_handoff()
    result = copy.deepcopy(prior)
    fallback_assessment = truncate_text(assessment, MAX_ASSESSMENT_CHARS)
    if fallback_assessment:
        result["assessment"] = fallback_assessment
    return validate_handoff(result)


def validate_handoff(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "assessment", "openLoops"}:
        raise DecisionHandoffError("decision handoff has an invalid shape")
    if value.get("schemaVersion") != HANDOFF_SCHEMA_VERSION:
        raise DecisionHandoffError("unsupported decision handoff schemaVersion")
    assessment = bounded_text(value.get("assessment"), "assessment", MAX_ASSESSMENT_CHARS, required=False)
    raw_loops = value.get("openLoops")
    if not isinstance(raw_loops, list) or len(raw_loops) > MAX_OPEN_LOOPS:
        raise DecisionHandoffError(f"openLoops must contain at most {MAX_OPEN_LOOPS} items")
    loops: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_loops:
        if not isinstance(raw, dict) or set(raw) != {"id", "objective", "nextAction", "status", "reason"}:
            raise DecisionHandoffError("persisted open loop has an invalid shape")
        loop_id = str(raw.get("id") or "")
        if not LOOP_ID_PATTERN.fullmatch(loop_id) or loop_id in seen:
            raise DecisionHandoffError("persisted open loop has an invalid or duplicate id")
        status = str(raw.get("status") or "")
        if status not in LOOP_STATUSES:
            raise DecisionHandoffError("persisted open loop has an invalid status")
        seen.add(loop_id)
        loops.append({
            "id": loop_id,
            "objective": bounded_text(raw.get("objective"), "objective", MAX_OBJECTIVE_CHARS),
            "nextAction": bounded_text(raw.get("nextAction"), "nextAction", MAX_NEXT_ACTION_CHARS),
            "status": status,
            "reason": bounded_text(raw.get("reason"), "reason", MAX_REASON_CHARS, required=False),
        })
    result = {"schemaVersion": HANDOFF_SCHEMA_VERSION, "assessment": assessment, "openLoops": loops}
    if handoff_chars(result) > MAX_HANDOFF_CHARS:
        raise DecisionHandoffError(f"decision handoff exceeds {MAX_HANDOFF_CHARS} characters")
    return result


def empty_handoff() -> dict[str, Any]:
    return {"schemaVersion": HANDOFF_SCHEMA_VERSION, "assessment": "", "openLoops": []}


def handoff_chars(value: dict[str, Any] | None) -> int:
    return len(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))) if value else 0


def make_loop_id(objective: str, unavailable: set[str]) -> str:
    stem = "L-" + hashlib.sha256(normalize_text(objective).encode("utf-8")).hexdigest()[:6]
    candidate = stem
    suffix = 2
    while candidate in unavailable:
        candidate = f"{stem}-{suffix}"
        suffix += 1
    return candidate


def bounded_text(value: Any, field: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise DecisionHandoffError(f"{field} must be a string")
    text = " ".join(value.split())
    if required and not text:
        raise DecisionHandoffError(f"{field} cannot be empty")
    if len(text) > maximum:
        raise DecisionHandoffError(f"{field} exceeds {maximum} characters")
    return text


def truncate_text(value: Any, maximum: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= maximum else text[: maximum - 3].rstrip() + "..."


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())
