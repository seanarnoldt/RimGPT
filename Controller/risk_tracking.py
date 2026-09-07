"""Compact deterministic health/environment risk triage and hysteresis."""

from __future__ import annotations

import copy
import json
from typing import Any


RISK_SCHEMA_VERSION = 1
MAX_TRACKED_RISKS = 12
MAX_RISK_CONTEXT_CHARS = 3_000
RECENT_RESOLUTION_CYCLES = 2
TRIAGE_ORDER = {"none": 0, "monitor": 1, "concerning": 2, "urgent": 3, "critical": 4}
TRACKED_CONDITION_MARKERS = (
    "heatstroke",
    "hypothermia",
    "bloodloss",
    "infection",
    "toxicbuildup",
)


def empty_risk_metadata(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": RISK_SCHEMA_VERSION,
        "identity": copy.deepcopy(identity),
        "conditions": [],
    }


def validate_risk_metadata(value: Any, identity: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") != RISK_SCHEMA_VERSION:
        raise ValueError("unsupported risk metadata")
    if value.get("identity") != identity:
        raise ValueError("risk metadata colony identity mismatch")

    conditions = []
    seen: set[str] = set()
    for raw in value.get("conditions", []):
        if not isinstance(raw, dict) or len(conditions) >= MAX_TRACKED_RISKS:
            continue
        pawn_id = short_text(raw.get("pawnId"), 64)
        condition = short_text(raw.get("condition"), 80)
        key = risk_key(pawn_id, condition)
        status = str(raw.get("status") or "")
        triage = str(raw.get("lastTriage") or "monitor")
        if not pawn_id or not condition or key in seen:
            continue
        if status not in ("active", "resolved") or triage not in TRIAGE_ORDER:
            continue
        seen.add(key)
        conditions.append({
            "key": key,
            "pawnId": pawn_id,
            "condition": condition,
            "status": status,
            "lastSeverity": bounded_float(raw.get("lastSeverity")),
            "peakSeverity": bounded_float(raw.get("peakSeverity")),
            "lastTriage": triage,
            "observedCycles": bounded_int(raw.get("observedCycles"), 1, 99),
            "cyclesSinceResolved": bounded_int(raw.get("cyclesSinceResolved"), 0, RECENT_RESOLUTION_CYCLES),
        })
    conditions.sort(key=lambda item: (item["pawnId"], item["condition"]))
    return {
        "schemaVersion": RISK_SCHEMA_VERSION,
        "identity": copy.deepcopy(identity),
        "conditions": conditions,
    }


def build_operational_risk_summary(
    baseline: Any,
    current: Any,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(current, dict):
        return empty_risk_summary()
    prior_conditions = index_metadata(metadata)
    before = extract_conditions(baseline)
    after = extract_conditions(current)
    items = []

    for key in sorted(after):
        condition = after[key]
        previous = before.get(key)
        stored = prior_conditions.get(key)
        trend_source = previous
        if trend_source is None and isinstance(stored, dict) and stored.get("status") == "resolved":
            trend_source = {"severity": stored.get("lastSeverity")}
        trend = severity_trend(trend_source, condition)
        triage = classify_condition(condition, trend)
        recurrence_suppressed = is_minor_recurrence(stored, condition)
        if recurrence_suppressed and TRIAGE_ORDER[triage] > TRIAGE_ORDER["monitor"]:
            triage = "monitor"
        item = {
            "pawnId": condition["pawnId"],
            "condition": condition["condition"],
            "severity": condition.get("severity"),
            "trend": trend,
            "triage": triage,
            "persistenceCycles": persistence_cycles(stored),
            "interruptNormalPriorities": triage in ("urgent", "critical"),
            "recommendedResponse": response_for_triage(triage),
        }
        if condition.get("currentJob"):
            item["currentJob"] = condition["currentJob"]
        if condition.get("vulnerable"):
            item["vulnerable"] = True
        if recurrence_suppressed:
            item["recentMinorRecurrence"] = True
        items.append(item)

    for key in sorted(set(before) - set(after)):
        condition = before[key]
        if TRIAGE_ORDER[classify_condition(condition, "improving")] > TRIAGE_ORDER["concerning"]:
            continue
        items.append({
            "pawnId": condition["pawnId"],
            "condition": condition["condition"],
            "severity": 0.0,
            "trend": "resolved",
            "triage": "monitor",
            "recentlyResolved": True,
            "interruptNormalPriorities": False,
            "recommendedResponse": "Do not reopen emergency handling unless the condition meaningfully worsens.",
        })

    items = sorted(
        items,
        key=lambda item: (-TRIAGE_ORDER.get(str(item.get("triage")), 0), str(item.get("pawnId")), str(item.get("condition"))),
    )
    original_count = len(items)
    items = items[:MAX_TRACKED_RISKS]
    overall = max((str(item.get("triage")) for item in items), key=lambda value: TRIAGE_ORDER.get(value, 0), default="none")
    result = {
        "overallTriage": overall,
        "interruptNormalPriorities": any(bool(item.get("interruptNormalPriorities")) for item in items),
        "items": items,
    }
    if items:
        environment = map_environment(current)
        if environment:
            result["environment"] = environment
        result["saferRecoverySpaceAvailable"] = has_temperature_safe_room(current)
    if len(items) < original_count:
        result["riskCount"] = original_count
        result["detailsTruncated"] = True
    if serialized_chars(result) > MAX_RISK_CONTEXT_CHARS:
        result["riskCount"] = original_count
        result["detailsTruncated"] = True
    while items and serialized_chars(result) > MAX_RISK_CONTEXT_CHARS:
        items.pop()
        result["overallTriage"] = max(
            (str(item.get("triage")) for item in items),
            key=lambda value: TRIAGE_ORDER.get(value, 0),
            default="none",
        )
        result["interruptNormalPriorities"] = any(
            bool(item.get("interruptNormalPriorities")) for item in items
        )
    return result


def update_risk_metadata(
    metadata: dict[str, Any],
    baseline: Any,
    current: Any,
) -> dict[str, Any]:
    identity = metadata.get("identity") if isinstance(metadata, dict) else None
    if not isinstance(identity, dict):
        raise ValueError("risk metadata has no colony identity")
    previous = index_metadata(metadata)
    before = extract_conditions(baseline)
    after = extract_conditions(current)
    conditions = []

    for key in sorted(after):
        condition = after[key]
        stored = previous.get(key, {})
        trend_source = before.get(key)
        if trend_source is None and stored.get("status") == "resolved":
            trend_source = {"severity": stored.get("lastSeverity")}
        trend = severity_trend(trend_source, condition)
        triage = classify_condition(condition, trend)
        if is_minor_recurrence(stored, condition) and TRIAGE_ORDER[triage] > TRIAGE_ORDER["monitor"]:
            triage = "monitor"
        severity = bounded_float(condition.get("severity"))
        conditions.append({
            "key": key,
            "pawnId": condition["pawnId"],
            "condition": condition["condition"],
            "status": "active",
            "lastSeverity": severity,
            "peakSeverity": max(severity, bounded_float(stored.get("peakSeverity"))),
            "lastTriage": triage,
            "observedCycles": bounded_int(stored.get("observedCycles"), 0, 98) + 1,
            "cyclesSinceResolved": 0,
        })

    for key in sorted(set(previous) - set(after)):
        stored = previous[key]
        resolved_cycles = (
            bounded_int(stored.get("cyclesSinceResolved"), 0, RECENT_RESOLUTION_CYCLES) + 1
            if stored.get("status") == "resolved"
            else 0
        )
        if resolved_cycles > RECENT_RESOLUTION_CYCLES:
            continue
        conditions.append({
            **copy.deepcopy(stored),
            "status": "resolved",
            "cyclesSinceResolved": resolved_cycles,
        })

    result = {
        "schemaVersion": RISK_SCHEMA_VERSION,
        "identity": copy.deepcopy(identity),
        "conditions": conditions[:MAX_TRACKED_RISKS],
    }
    return validate_risk_metadata(result, identity)


def extract_conditions(state: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(state, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for pawn in dict_list(state.get("colonists")):
        pawn_id = short_text(pawn.get("id"), 64)
        if not pawn_id:
            continue
        health = pawn.get("health") if isinstance(pawn.get("health"), dict) else {}
        immediate = bool(
            health.get("downed")
            or health.get("dead")
            or number(health.get("bleedingRate")) >= 0.15
            or number(health.get("pain")) >= 0.6
        )
        vulnerable = immediate or number(pawn.get("age")) >= 70
        current_job = pawn.get("currentJob") if isinstance(pawn.get("currentJob"), dict) else {}
        for hediff in dict_list(health.get("hediffs")):
            condition = short_text(hediff.get("defName") or hediff.get("label"), 80)
            if not is_tracked_condition(condition):
                continue
            severity = bounded_float(hediff.get("severity"))
            key = risk_key(pawn_id, condition)
            existing = result.get(key)
            if existing is None or severity > existing["severity"]:
                result[key] = {
                    "pawnId": pawn_id,
                    "condition": condition,
                    "severity": severity,
                    "vulnerable": vulnerable,
                    "currentJob": short_text(current_job.get("defName"), 80) or None,
                }
        if immediate and not any(item["pawnId"] == pawn_id for item in result.values()):
            result[risk_key(pawn_id, "ImmediateHealthDanger")] = {
                "pawnId": pawn_id,
                "condition": "ImmediateHealthDanger",
                "severity": max(number(health.get("bleedingRate")), number(health.get("pain"))),
                "vulnerable": True,
                "immediate": True,
            }
    return result


def classify_condition(condition: dict[str, Any], trend: str) -> str:
    severity = bounded_float(condition.get("severity"))
    if condition.get("immediate") or (condition.get("vulnerable") and severity >= 0.5) or severity >= 0.8:
        return "critical"
    if severity >= 0.4 or (condition.get("vulnerable") and severity >= 0.2):
        return "urgent"
    if trend == "worsening" and severity >= 0.2:
        return "urgent"
    if severity >= 0.1 or (trend == "worsening" and severity >= 0.05):
        return "concerning"
    return "monitor"


def severity_trend(before: dict[str, Any] | None, after: dict[str, Any]) -> str:
    if before is None:
        return "new"
    old_value = bounded_float(before.get("severity"))
    new_value = bounded_float(after.get("severity"))
    meaningful = max(0.01, old_value * 0.25)
    if new_value - old_value >= meaningful:
        return "worsening"
    if old_value - new_value >= meaningful:
        return "improving"
    return "stable"


def is_minor_recurrence(stored: dict[str, Any] | None, condition: dict[str, Any]) -> bool:
    if not isinstance(stored, dict) or stored.get("status") != "resolved":
        return False
    if stored.get("lastTriage") not in ("monitor", "concerning"):
        return False
    return bounded_float(condition.get("severity")) <= max(0.1, bounded_float(stored.get("lastSeverity")) + 0.05)


def persistence_cycles(stored: dict[str, Any] | None) -> int:
    if not isinstance(stored, dict) or stored.get("status") != "active":
        return 1
    return bounded_int(stored.get("observedCycles"), 1, 98) + 1


def response_for_triage(triage: str) -> str:
    if triage == "critical":
        return "Intervene immediately; tactical control is permitted when it is the least disruptive effective option."
    if triage == "urgent":
        return "Intervene promptly using the least disruptive effective option."
    if triage == "concerning":
        return "Monitor closely and use normal scheduling or area controls if exposure continues."
    return "Monitor while productive work continues; reassess only after meaningful worsening."


def empty_risk_summary() -> dict[str, Any]:
    return {"overallTriage": "none", "interruptNormalPriorities": False, "items": []}


def map_environment(state: dict[str, Any]) -> dict[str, Any]:
    map_state = state.get("map") if isinstance(state.get("map"), dict) else {}
    environment = map_state.get("environment") if isinstance(map_state.get("environment"), dict) else {}
    result = {}
    for field in ("outdoorTemperature", "season", "weather"):
        if environment.get(field) is not None:
            result[field] = copy.deepcopy(environment[field])
    return result


def has_temperature_safe_room(state: dict[str, Any]) -> bool:
    for building in dict_list(state.get("buildings")):
        room = building.get("room") if isinstance(building.get("room"), dict) else {}
        if room.get("enclosed") and room.get("suitableForTemperatureControl"):
            return True
    return False


def index_metadata(metadata: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(metadata, dict):
        return {}
    return {
        str(item.get("key")): item
        for item in metadata.get("conditions", [])
        if isinstance(item, dict) and item.get("key")
    }


def risk_key(pawn_id: Any, condition: Any) -> str:
    return f"{str(pawn_id)}|{str(condition).lower()}"


def is_tracked_condition(condition: str) -> bool:
    normalized = condition.lower().replace("_", "").replace(" ", "")
    return any(marker in normalized for marker in TRACKED_CONDITION_MARKERS)


def bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return minimum


def bounded_float(value: Any) -> float:
    return round(max(0.0, min(100.0, number(value))), 4)


def number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def short_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def serialized_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str))
