"""Bounded progress evidence and colony-scoped stall recovery metadata."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from typing import Any


STALL_SCHEMA_VERSION = 1
MAX_PROGRESS_SIGNALS = 24
MAX_STALL_LOOPS = 6
MAX_VERIFICATION_STRATEGIES = 8
MAX_STALL_CONTEXT_CHARS = 4_000
VERIFICATION_TOOLS = {"inspect_room_at", "inspect_map", "get_colony_state"}


def build_progress_signals(baseline: Any, current: Any) -> dict[str, Any]:
    if not compatible_snapshots(baseline, current):
        return {"relevantStateChanged": False, "categories": [], "signals": []}

    signals: list[dict[str, Any]] = []
    before_buildings = index_by_id(baseline.get("buildings"))
    after_buildings = index_by_id(current.get("buildings"))
    append_construction_transitions(signals, before_buildings, after_buildings)
    append_construction_counts(signals, before_buildings, after_buildings)
    append_room_transitions(signals, before_buildings, after_buildings)
    append_labor_progress(signals, baseline, current)

    total = len(signals)
    bounded = signals[:MAX_PROGRESS_SIGNALS]
    result: dict[str, Any] = {
        "relevantStateChanged": bool(signals),
        "categories": sorted({str(item["category"]) for item in signals}),
        "signals": bounded,
    }
    if total > len(bounded):
        result["signalCount"] = total
        result["detailsTruncated"] = True
    return result


def empty_stall_metadata(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": STALL_SCHEMA_VERSION,
        "identity": copy.deepcopy(identity),
        "loops": [],
        "failedVerificationStrategies": [],
    }


def validate_stall_metadata(value: Any, identity: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") != STALL_SCHEMA_VERSION:
        raise ValueError("unsupported stall metadata")
    if value.get("identity") != identity:
        raise ValueError("stall metadata colony identity mismatch")

    loops = []
    seen_loops: set[str] = set()
    for raw in value.get("loops", []):
        if not isinstance(raw, dict) or len(loops) >= MAX_STALL_LOOPS:
            continue
        loop_id = short_text(raw.get("id"), 32)
        fingerprint = short_text(raw.get("nextActionFingerprint"), 24)
        if not loop_id or not fingerprint or loop_id in seen_loops:
            continue
        seen_loops.add(loop_id)
        loops.append({
            "id": loop_id,
            "nextActionFingerprint": fingerprint,
            "repeatedNextActionCycles": bounded_int(raw.get("repeatedNextActionCycles"), 1, 99),
            "noRelevantProgressCycles": bounded_int(raw.get("noRelevantProgressCycles"), 0, 99),
        })

    strategies = []
    seen_strategies: set[str] = set()
    for raw in value.get("failedVerificationStrategies", []):
        if not isinstance(raw, dict) or len(strategies) >= MAX_VERIFICATION_STRATEGIES:
            continue
        key = short_text(raw.get("key"), 24)
        tool = short_text(raw.get("tool"), 40)
        strategy = short_text(raw.get("strategy"), 160)
        if not key or tool not in VERIFICATION_TOOLS or not strategy or key in seen_strategies:
            continue
        seen_strategies.add(key)
        strategies.append({
            "key": key,
            "tool": tool,
            "strategy": strategy,
            "consecutiveFailures": bounded_int(raw.get("consecutiveFailures"), 1, 99),
        })
    return {
        "schemaVersion": STALL_SCHEMA_VERSION,
        "identity": copy.deepcopy(identity),
        "loops": loops,
        "failedVerificationStrategies": strategies,
    }


def update_open_loop_stalls(
    metadata: dict[str, Any],
    previous_handoff: dict[str, Any] | None,
    next_handoff: dict[str, Any],
    progress: dict[str, Any],
) -> dict[str, Any]:
    result = copy.deepcopy(metadata)
    prior_state = {item["id"]: item for item in result.get("loops", []) if isinstance(item, dict)}
    previous_loops = {
        item["id"]: item for item in (previous_handoff or {}).get("openLoops", []) if isinstance(item, dict) and item.get("id")
    }
    progress_categories = set(str(item) for item in progress.get("categories", []))
    loops = []
    for item in next_handoff.get("openLoops", [])[:MAX_STALL_LOOPS]:
        loop_id = str(item.get("id") or "")
        fingerprint = text_fingerprint(item.get("nextAction"))
        prior = prior_state.get(loop_id, {})
        previous = previous_loops.get(loop_id)
        same_action = previous is not None and text_fingerprint(previous.get("nextAction")) == fingerprint
        repeated = 1
        if same_action:
            repeated = bounded_int(prior.get("repeatedNextActionCycles"), 1, 98) + 1
        relevant = bool(loop_categories(item) & progress_categories)
        no_progress = (
            bounded_int(prior.get("noRelevantProgressCycles"), 0, 98) + 1
            if same_action and not relevant
            else 0
        )
        loops.append({
            "id": loop_id,
            "nextActionFingerprint": fingerprint,
            "repeatedNextActionCycles": repeated,
            "noRelevantProgressCycles": no_progress,
        })
    result["loops"] = loops
    return result


def record_verification_outcome(
    metadata: dict[str, Any], tool: str, arguments: dict[str, Any], *, success: bool
) -> dict[str, Any]:
    if tool not in VERIFICATION_TOOLS:
        return copy.deepcopy(metadata)
    result = copy.deepcopy(metadata)
    strategy = verification_strategy(tool, arguments)
    key = hashlib.sha256(strategy.encode("utf-8")).hexdigest()[:16]
    existing = {
        item.get("key"): item
        for item in result.get("failedVerificationStrategies", [])
        if isinstance(item, dict)
    }
    if success:
        existing.pop(key, None)
    else:
        previous = existing.get(key, {})
        existing[key] = {
            "key": key,
            "tool": tool,
            "strategy": strategy,
            "consecutiveFailures": bounded_int(previous.get("consecutiveFailures"), 0, 98) + 1,
        }
    result["failedVerificationStrategies"] = sorted(
        existing.values(), key=lambda item: (-int(item.get("consecutiveFailures", 0)), str(item.get("key")))
    )[:MAX_VERIFICATION_STRATEGIES]
    return result


def build_stall_context(
    metadata: dict[str, Any] | None,
    handoff: dict[str, Any] | None,
    progress: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    stored_loops = {item["id"]: item for item in metadata.get("loops", []) if isinstance(item, dict)}
    progress_by_category: dict[str, list[str]] = {}
    for signal in progress.get("signals", []):
        if not isinstance(signal, dict):
            continue
        progress_by_category.setdefault(str(signal.get("category") or ""), []).append(str(signal.get("type") or "progress"))

    loops = []
    for loop in (handoff or {}).get("openLoops", [])[:MAX_STALL_LOOPS]:
        stored = stored_loops.get(loop.get("id"), {})
        relevant_types = sorted({
            signal_type
            for category in loop_categories(loop)
            for signal_type in progress_by_category.get(category, [])
        })[:6]
        repeated = bounded_int(stored.get("repeatedNextActionCycles"), 1, 99)
        no_progress = bounded_int(stored.get("noRelevantProgressCycles"), 0, 99)
        entry = {
            "id": loop.get("id"),
            "repeatedNextActionCycles": repeated,
            "noRelevantProgressCycles": no_progress,
            "progressSinceBaseline": relevant_types,
            "stalled": repeated >= 2 and no_progress >= 1 and not relevant_types,
        }
        if relevant_types:
            entry["guidance"] = "Verify cheaply, then advance or resolve this prerequisite/open loop."
        elif entry["stalled"]:
            entry["guidance"] = "Do not repeat the same action unchanged; narrow verification, change prerequisite, or record the blocker."
        if relevant_types or repeated >= 2 or no_progress >= 1:
            loops.append(entry)

    result = {
        "openLoops": loops,
        "failedVerificationStrategies": copy.deepcopy(metadata.get("failedVerificationStrategies", []))[:MAX_VERIFICATION_STRATEGIES],
    }
    if not result["openLoops"] and not result["failedVerificationStrategies"]:
        return None
    if serialized_chars(result) > MAX_STALL_CONTEXT_CHARS:
        result["failedVerificationStrategies"] = result["failedVerificationStrategies"][:4]
        result["truncated"] = True
    return result


def append_construction_transitions(
    signals: list[dict[str, Any]], before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> None:
    added = set(after) - set(before)
    by_key: dict[tuple[Any, ...], list[str]] = {}
    for thing_id in added:
        by_key.setdefault(construction_key(after[thing_id]), []).append(thing_id)
    consumed: set[str] = set()
    for old_id in sorted(set(before) - set(after)):
        source = before[old_id]
        candidates = [item for item in by_key.get(construction_key(source), []) if item not in consumed]
        candidates = [item for item in candidates if construction_rank(after[item]) > construction_rank(source)]
        if len(candidates) != 1:
            continue
        new_id = candidates[0]
        consumed.add(new_id)
        signals.append({
            "type": "constructionAdvanced",
            "category": "construction",
            "defName": base_construction_def(after[new_id]),
            "position": copy.deepcopy(after[new_id].get("position")),
            "from": {"id": old_id, "stage": source.get("type")},
            "to": {"id": new_id, "stage": after[new_id].get("type")},
        })


def append_construction_counts(
    signals: list[dict[str, Any]], before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> None:
    old_counts = Counter((base_construction_def(item), str(item.get("type") or "unknown")) for item in before.values())
    new_counts = Counter((base_construction_def(item), str(item.get("type") or "unknown")) for item in after.values())
    for key in sorted(set(old_counts) | set(new_counts)):
        if old_counts[key] == new_counts[key]:
            continue
        signals.append({
            "type": "constructionCountChanged",
            "category": "construction",
            "defName": key[0],
            "stage": key[1],
            "from": old_counts[key],
            "to": new_counts[key],
        })


def append_room_transitions(
    signals: list[dict[str, Any]], before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> None:
    grouped: dict[str, dict[str, Any]] = {}
    for thing_id in sorted(set(before) & set(after)):
        old_room = room_summary(before[thing_id].get("room"))
        new_room = room_summary(after[thing_id].get("room"))
        if room_semantics(old_room) == room_semantics(new_room):
            continue
        key = canonical_json({"from": old_room, "to": new_room})
        entry = grouped.setdefault(key, {
            "type": "roomTopologyChanged",
            "category": "room",
            "position": copy.deepcopy(after[thing_id].get("position")),
            "representativeThingId": thing_id,
            "from": old_room,
            "to": new_room,
            "affectedThingCount": 0,
        })
        entry["affectedThingCount"] += 1
    signals.extend(grouped[key] for key in sorted(grouped))


def append_labor_progress(signals: list[dict[str, Any]], baseline: dict[str, Any], current: dict[str, Any]) -> None:
    old_labor = labor_state(baseline)
    new_labor = labor_state(current)
    old_pending = old_labor.get("pendingWork") if isinstance(old_labor.get("pendingWork"), dict) else {}
    new_pending = new_labor.get("pendingWork") if isinstance(new_labor.get("pendingWork"), dict) else {}
    for field in ("blueprints", "frames", "haulables", "activeBills", "designations"):
        old_value, new_value = integer(old_pending.get(field)), integer(new_pending.get(field))
        if old_value != new_value:
            signals.append({"type": "pendingWorkChanged", "category": "work", "work": field, "from": old_value, "to": new_value})

    old_designations = designation_counts(old_pending)
    new_designations = designation_counts(new_pending)
    for def_name in sorted(set(old_designations) | set(new_designations)):
        if old_designations.get(def_name, 0) != new_designations.get(def_name, 0):
            signal = {
                "type": "designationCountChanged",
                "category": "designation",
                "defName": def_name,
                "from": old_designations.get(def_name, 0),
                "to": new_designations.get(def_name, 0),
            }
            if signal["to"] < signal["from"]:
                signal["completedOrRemoved"] = signal["from"] - signal["to"]
            signals.append(signal)

    old_blockers = {canonical_json(item): item for item in dict_list(old_labor.get("obviousBlockers"))}
    new_blockers = {canonical_json(item): item for item in dict_list(new_labor.get("obviousBlockers"))}
    for key in sorted(set(old_blockers) - set(new_blockers)):
        signals.append({"type": "blockerCleared", "category": "blocker", "blocker": copy.deepcopy(old_blockers[key])})


def compatible_snapshots(before: Any, after: Any) -> bool:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return False
    old_game = before.get("game") if isinstance(before.get("game"), dict) else {}
    new_game = after.get("game") if isinstance(after.get("game"), dict) else {}
    return (
        before.get("schemaVersion") == after.get("schemaVersion")
        and old_game.get("colonyLineageId")
        and old_game.get("colonyLineageId") == new_game.get("colonyLineageId")
        and old_game.get("currentMapId") == new_game.get("currentMapId")
    )


def loop_categories(loop: dict[str, Any]) -> set[str]:
    text = " ".join((str(loop.get("objective") or ""), str(loop.get("nextAction") or ""))).lower()
    categories = set()
    keyword_groups = {
        "room": ("room", "enclos", "roof", "temperature", "cooler", "heater", "shelter"),
        "construction": ("build", "wall", "blueprint", "frame", "construct"),
        "designation": ("designat", "mine", "harvest", "cut"),
        "blocker": ("block", "prerequisite"),
        "work": ("work", "haul", "research", "bill"),
    }
    for category, keywords in keyword_groups.items():
        if any(keyword in text for keyword in keywords):
            categories.add(category)
    return categories


def verification_strategy(tool: str, arguments: dict[str, Any]) -> str:
    if tool == "inspect_room_at":
        return f"inspect_room_at({integer(arguments.get('x'))},{integer(arguments.get('z'))})"
    if tool == "inspect_map":
        return (
            f"inspect_map({integer(arguments.get('min_x'))},{integer(arguments.get('min_z'))},"
            f"{integer(arguments.get('max_x'))},{integer(arguments.get('max_z'))})"
        )
    if tool == "get_colony_state":
        return "get_colony_state(" + short_text(arguments.get("section"), 40) + ")"
    return short_text(tool, 40)


def construction_key(item: dict[str, Any]) -> tuple[Any, ...]:
    position = item.get("position") if isinstance(item.get("position"), dict) else {}
    return (base_construction_def(item), position.get("x"), position.get("z"), item.get("rotation"))


def base_construction_def(item: dict[str, Any]) -> str:
    value = str(item.get("defName") or "unknown")
    for prefix in ("Blueprint_", "Frame_"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def construction_rank(item: dict[str, Any]) -> int:
    return {"blueprint": 1, "frame": 2, "building": 3}.get(str(item.get("type")), 0)


def room_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: copy.deepcopy(value.get(key))
        for key in (
            "id", "indoors", "enclosed", "usesOutdoorTemperature", "suitableForTemperatureControl",
            "cellCount", "roofedCellCount", "roofCoverage", "bounds",
        )
        if value.get(key) is not None
    }


def room_semantics(value: dict[str, Any] | None) -> str:
    if not isinstance(value, dict):
        return canonical_json(value)
    return canonical_json({key: item for key, item in value.items() if key != "id"})


def labor_state(state: dict[str, Any]) -> dict[str, Any]:
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    return operations.get("labor") if isinstance(operations.get("labor"), dict) else {}


def designation_counts(pending: dict[str, Any]) -> dict[str, int]:
    return {
        str(item.get("defName")): integer(item.get("count"))
        for item in dict_list(pending.get("byDesignationType"))
        if item.get("defName")
    }


def index_by_id(value: Any) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in dict_list(value) if item.get("id")}


def dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def text_fingerprint(value: Any) -> str:
    normalized = " ".join(str(value or "").lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def short_text(value: Any, maximum: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= maximum else text[: maximum - 3].rstrip() + "..."


def bounded_int(value: Any, minimum: int, maximum: int) -> int:
    number = value if isinstance(value, int) and not isinstance(value, bool) else minimum
    return max(minimum, min(maximum, number))


def integer(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def serialized_chars(value: Any) -> int:
    return len(canonical_json(value))
