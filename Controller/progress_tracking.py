"""Bounded progress evidence and colony-scoped stall recovery metadata."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from typing import Any


STALL_SCHEMA_VERSION = 1
MAX_PROGRESS_SIGNALS = 24
MAX_STALL_LOOPS = 6
MAX_STALL_PROJECT_TASKS = 24
MAX_VERIFICATION_STRATEGIES = 8
MAX_STALL_CONTEXT_CHARS = 4_000
VERIFICATION_TOOLS = {"inspect_room_at", "inspect_map", "get_colony_state"}


def build_progress_signals(baseline: Any, current: Any) -> dict[str, Any]:
    completion = build_completion_evidence(current)
    if not compatible_snapshots(baseline, current):
        return {
            "relevantStateChanged": False,
            "categories": [],
            "signals": [],
            "completionEvidence": completion,
        }

    signals: list[dict[str, Any]] = []
    before_buildings = index_by_id(baseline.get("buildings"))
    after_buildings = index_by_id(current.get("buildings"))
    append_construction_transitions(signals, before_buildings, after_buildings)
    append_construction_counts(signals, before_buildings, after_buildings)
    append_room_transitions(signals, before_buildings, after_buildings)
    append_labor_progress(signals, baseline, current)
    append_growing_progress(signals, baseline, current)
    append_research_progress(signals, baseline, current)

    total = len(signals)
    bounded = signals[:MAX_PROGRESS_SIGNALS]
    result: dict[str, Any] = {
        "relevantStateChanged": bool(signals),
        "categories": sorted({str(item["category"]) for item in signals}),
        "signals": bounded,
        "completionEvidence": completion,
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
        "projectTasks": [],
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
    project_tasks = []
    seen_tasks: set[str] = set()
    for raw in value.get("projectTasks", []):
        if not isinstance(raw, dict) or len(project_tasks) >= MAX_STALL_PROJECT_TASKS:
            continue
        task_id = short_text(raw.get("id"), 32)
        fingerprint = short_text(raw.get("fingerprint"), 24)
        if not task_id.startswith("T-") or not fingerprint or task_id in seen_tasks:
            continue
        seen_tasks.add(task_id)
        project_tasks.append({
            "id": task_id,
            "fingerprint": fingerprint,
            "repeatedCycles": bounded_int(raw.get("repeatedCycles"), 1, 99),
            "noRelevantProgressCycles": bounded_int(raw.get("noRelevantProgressCycles"), 0, 99),
        })
    return {
        "schemaVersion": STALL_SCHEMA_VERSION,
        "identity": copy.deepcopy(identity),
        "loops": loops,
        "projectTasks": project_tasks,
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
        satisfied = bool(authoritative_completion_for(item, progress))
        no_progress = (
            bounded_int(prior.get("noRelevantProgressCycles"), 0, 98) + 1
            if same_action and not relevant and not satisfied
            else 0
        )
        loops.append({
            "id": loop_id,
            "nextActionFingerprint": fingerprint,
            "repeatedNextActionCycles": repeated,
            "noRelevantProgressCycles": no_progress,
        })
    result["loops"] = loops
    result["projectTasks"] = update_project_task_stalls(
        result.get("projectTasks"), previous_handoff, next_handoff, progress
    )
    return result


def update_project_task_stalls(
    prior_values: Any,
    previous_handoff: dict[str, Any] | None,
    next_handoff: dict[str, Any],
    progress: dict[str, Any],
) -> list[dict[str, Any]]:
    prior = {
        str(item.get("id")): item
        for item in prior_values or []
        if isinstance(item, dict) and item.get("id")
    }
    previous = project_tasks_by_id(previous_handoff)
    current = project_tasks_by_id(next_handoff)
    progress_categories = set(str(item) for item in progress.get("categories", []))
    result = []
    for task_id in sorted(current):
        task = current[task_id]
        if task.get("status") == "completed":
            continue
        fingerprint = project_task_fingerprint(task)
        old_task = previous.get(task_id)
        old_state = prior.get(task_id, {})
        same = old_task is not None and project_task_fingerprint(old_task) == fingerprint
        relevant = bool(project_task_categories(task) & progress_categories)
        satisfied = bool(authoritative_completion_for(task, progress))
        repeated = bounded_int(old_state.get("repeatedCycles"), 1, 98) + 1 if same else 1
        no_progress = (
            bounded_int(old_state.get("noRelevantProgressCycles"), 0, 98) + 1
            if same and not relevant and not satisfied
            else 0
        )
        result.append({
            "id": task_id,
            "fingerprint": fingerprint,
            "repeatedCycles": repeated,
            "noRelevantProgressCycles": no_progress,
        })
    return result[:MAX_STALL_PROJECT_TASKS]


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
        completion_evidence = authoritative_completion_for(loop, progress)
        entry = {
            "id": loop.get("id"),
            "repeatedNextActionCycles": repeated,
            "noRelevantProgressCycles": no_progress,
            "progressSinceBaseline": relevant_types,
            "stalled": repeated >= 2 and no_progress >= 1 and not relevant_types and not completion_evidence,
        }
        if completion_evidence:
            entry["authoritativeCompletionEvidence"] = completion_evidence
            entry["guidance"] = "Authoritative state indicates this phase may be satisfied; advance or resolve it instead of treating inactivity as a stall."
        elif relevant_types:
            entry["guidance"] = "Verify cheaply, then advance or resolve this prerequisite/open loop."
        elif entry["stalled"]:
            entry["guidance"] = "Do not repeat the same action unchanged; narrow verification, change prerequisite, or record the blocker."
        if completion_evidence or relevant_types or repeated >= 2 or no_progress >= 1:
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


def append_growing_progress(signals: list[dict[str, Any]], baseline: dict[str, Any], current: dict[str, Any]) -> None:
    before = growing_zones_by_id(baseline)
    after = growing_zones_by_id(current)
    for zone_id in sorted(set(before) & set(after)):
        old = before[zone_id]
        new = after[zone_id]
        old_state = growing_phase(old)
        new_state = growing_phase(new)
        old_planted = integer(old.get("plantedCells"))
        new_planted = integer(new.get("plantedCells"))
        if old_state == new_state and old_planted == new_planted:
            continue
        signals.append({
            "type": "growingZoneAdvanced",
            "category": "growing",
            "zoneId": zone_id,
            "plantDef": new.get("plantDef"),
            "from": {"state": old_state, "plantedCells": old_planted},
            "to": {"state": new_state, "plantedCells": new_planted},
            "plantingComplete": new.get("plantingComplete") is True,
        })


def append_research_progress(signals: list[dict[str, Any]], baseline: dict[str, Any], current: dict[str, Any]) -> None:
    old_research = baseline.get("research") if isinstance(baseline.get("research"), dict) else {}
    new_research = current.get("research") if isinstance(current.get("research"), dict) else {}
    old_current = old_research.get("current") if isinstance(old_research.get("current"), dict) else None
    new_current = new_research.get("current") if isinstance(new_research.get("current"), dict) else None
    old_completed = {str(item.get("defName")) for item in dict_list(old_research.get("completed")) if item.get("defName")}
    new_completed = {str(item.get("defName")) for item in dict_list(new_research.get("completed")) if item.get("defName")}
    for def_name in sorted(new_completed - old_completed):
        signals.append({"type": "researchCompleted", "category": "research", "defName": def_name})
    if old_current is None or new_current is None or old_current.get("defName") != new_current.get("defName"):
        return
    old_progress = numeric(old_current.get("progress"))
    new_progress = numeric(new_current.get("progress"))
    if new_progress > old_progress:
        signals.append({
            "type": "researchAdvanced",
            "category": "research",
            "defName": new_current.get("defName"),
            "from": old_progress,
            "to": new_progress,
            "cost": numeric(new_current.get("cost")),
        })


def build_completion_evidence(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    zones = [growing_zone_summary(item) for item in growing_zones(state)]
    buildings = dict_list(state.get("buildings"))
    completed_buildings = [item for item in buildings if item.get("type") == "building"]
    pending_buildings = [item for item in buildings if item.get("type") in ("blueprint", "frame")]
    completed_by_def = Counter(base_construction_def(item) for item in completed_buildings)
    pending_by_def = Counter(base_construction_def(item) for item in pending_buildings)
    room_keys = set()
    for item in completed_buildings:
        room = item.get("room") if isinstance(item.get("room"), dict) else None
        if room_is_completed_shelter(room):
            room_keys.add(room_identity(room))

    research = state.get("research") if isinstance(state.get("research"), dict) else {}
    current_research = research.get("current") if isinstance(research.get("current"), dict) else None
    completed_research = [
        {"defName": item.get("defName"), "label": item.get("label")}
        for item in dict_list(research.get("completed"))[:40]
    ]
    labor = labor_state(state)
    pending = labor.get("pendingWork") if isinstance(labor.get("pendingWork"), dict) else {}
    hauling_known = "haulables" in pending
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    beds = [item for item in dict_list(operations.get("beds")) if not item.get("medical") and not item.get("forPrisoners")]
    for bed in beds:
        room = bed.get("room") if isinstance(bed.get("room"), dict) else None
        if room_is_completed_shelter(room):
            room_keys.add(room_identity(room))
    colonist_count = integer((state.get("colony") or {}).get("colonistCount")) if isinstance(state.get("colony"), dict) else len(dict_list(state.get("colonists")))
    return {
        "growing": {
            "zones": zones[:20],
            "plantingCompleteZones": sum(1 for item in zones if item.get("plantingComplete") is True),
            "unfinishedPlantingZones": sum(1 for item in zones if item.get("plantingComplete") is not True),
        },
        "construction": {
            "pendingBlueprints": integer(pending.get("blueprints")),
            "pendingFrames": integer(pending.get("frames")),
            "completedByDef": [
                {"defName": key, "count": completed_by_def[key]}
                for key in sorted(completed_by_def)[:30]
            ],
            "pendingByDef": [
                {"defName": key, "count": pending_by_def[key]}
                for key in sorted(pending_by_def)[:30]
            ],
            "enclosedRoofedRooms": len(room_keys),
        },
        "research": {
            "active": copy.deepcopy(current_research),
            "completed": completed_research,
            "completedCount": len(dict_list(research.get("completed"))),
        },
        "hauling": {
            "pendingHaulables": integer(pending.get("haulables")),
            "backlogClear": integer(pending.get("haulables")) == 0 if hauling_known else None,
        },
        "shelter": {
            "enclosedRoofedRooms": len(room_keys),
            "usableBeds": len(beds),
            "missingBeds": max(0, colonist_count - len(beds)),
        },
    }


def authoritative_completion_for(item: dict[str, Any], progress: dict[str, Any]) -> list[str]:
    completion = progress.get("completionEvidence") if isinstance(progress.get("completionEvidence"), dict) else {}
    objective = {"objective": item.get("objective"), "key": item.get("key")}
    categories = loop_categories(objective)
    text = normalized_domain_text(objective)
    evidence = []

    growing = completion.get("growing") if isinstance(completion.get("growing"), dict) else {}
    zones = dict_list(growing.get("zones"))
    matched_zones = matching_growing_zones(text, zones)
    specific_growing_target = growing_objective_is_specific(text)
    relevant_zones = matched_zones if specific_growing_target else zones
    if (
        "growing" in categories
        and "harvest" not in text
        and relevant_zones
        and all(zone.get("plantingComplete") is True for zone in relevant_zones)
    ):
        crop_names = sorted({str(zone.get("plantDef")) for zone in relevant_zones if zone.get("plantDef")})
        crop = " (" + ", ".join(crop_names[:3]) + ")" if crop_names else ""
        evidence.append("planting complete; crops are growing or harvestable" + crop)

    construction = completion.get("construction") if isinstance(completion.get("construction"), dict) else {}
    shelter = completion.get("shelter") if isinstance(completion.get("shelter"), dict) else {}
    if "room" in categories and generic_shelter_objective(text) and integer(shelter.get("enclosedRoofedRooms")) > 0:
        evidence.append("an enclosed substantially roofed room is authoritatively present")
    if "beds" in categories and aggregate_sleep_capacity_objective(text) and integer(shelter.get("missingBeds")) == 0:
        evidence.append("usable bed capacity meets current colonist count")
    completed_defs = [str(entry.get("defName")) for entry in dict_list(construction.get("completedByDef"))]
    pending_defs = {str(entry.get("defName")) for entry in dict_list(construction.get("pendingByDef"))}
    matched_building = matching_domain_name(text, completed_defs)
    if matched_building and matched_building not in pending_defs:
        evidence.append("completed building present: " + matched_building)
    for signal in dict_list(progress.get("signals")):
        if signal.get("type") != "constructionAdvanced" or signal.get("to", {}).get("stage") != "building":
            continue
        def_name = str(signal.get("defName") or "")
        if def_name and normalize_identifier(def_name) in normalize_identifier(text):
            evidence.append("ordered construction completed: " + def_name)
            break

    research = completion.get("research") if isinstance(completion.get("research"), dict) else {}
    completed_projects = dict_list(research.get("completed"))
    matched_research = matching_domain_entry(text, completed_projects)
    if "research" in categories and matched_research:
        evidence.append("research completed: " + str(matched_research.get("defName")))

    hauling = completion.get("hauling") if isinstance(completion.get("hauling"), dict) else {}
    if "hauling" in categories and hauling.get("backlogClear") is True:
        evidence.append("no currently relevant hauling backlog remains")
    return evidence[:4]


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
    text = normalized_domain_text(loop)
    categories = set()
    keyword_groups = {
        "room": ("room", "enclos", "roof", "temperature", "shelter"),
        "construction": ("build", "wall", "blueprint", "frame", "construct"),
        "designation": ("designat", "mine", "harvest", "cut"),
        "blocker": ("block", "prerequisite"),
        "work": ("work", "haul", "research", "bill", "sow", "plant"),
        "growing": ("grow", "plant", "sow", "crop", "field"),
        "research": ("research",),
        "hauling": ("haul", "stockpile", "storage"),
        "beds": ("bed", "sleep"),
    }
    for category, keywords in keyword_groups.items():
        if any(keyword in text for keyword in keywords):
            categories.add(category)
    return categories


def growing_zones(state: dict[str, Any]) -> list[dict[str, Any]]:
    map_state = state.get("map") if isinstance(state.get("map"), dict) else {}
    return [item for item in dict_list(map_state.get("zones")) if item.get("type") == "growing"]


def growing_zones_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in growing_zones(state) if item.get("id")}


def growing_phase(zone: dict[str, Any]) -> str:
    explicit = str(zone.get("growingState") or "")
    if explicit in ("empty", "partiallyPlanted", "planted", "harvestable", "mixed"):
        return explicit
    if integer(zone.get("harvestableCells")) > 0:
        return "harvestable"
    if zone.get("plantingComplete") is True:
        return "planted"
    if integer(zone.get("plantedCells")) > 0:
        return "partiallyPlanted"
    return "empty"


def growing_zone_summary(zone: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": zone.get("id"),
        "plantDef": zone.get("plantDef"),
        "cellCount": integer(zone.get("cellCount")),
        "observedCells": integer(zone.get("observedCells")),
        "plantedCells": integer(zone.get("plantedCells")),
        "unsownEligibleCells": integer(zone.get("unsownEligibleCells")),
        "growingCells": integer(zone.get("growingCells")),
        "harvestableCells": integer(zone.get("harvestableCells")),
        "plantingComplete": zone.get("plantingComplete") is True,
        "state": growing_phase(zone),
    }


def matching_growing_zones(text: str, zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_text = normalize_identifier(text)
    return [
        zone for zone in zones
        if any(
            normalized and normalized in normalized_text
            for normalized in (
                normalize_identifier(zone.get("id")),
                normalize_identifier(zone.get("plantDef")),
                crop_name_from_def(zone.get("plantDef")),
            )
        )
    ]


def growing_objective_is_specific(text: str) -> bool:
    words = re.findall(r"[a-z0-9]+", text.lower())
    generic_words = {
        "a", "an", "and", "appropriate", "crop", "crops", "create", "establish", "farm", "farming",
        "field", "fields", "food", "for", "grow", "growing", "maintain", "of", "plant", "planting",
        "renewable", "sow", "sowing", "the", "to", "zone", "zones",
    }
    return any(word not in generic_words for word in words)


def crop_name_from_def(value: Any) -> str:
    normalized = normalize_identifier(value)
    return normalized[5:] if normalized.startswith("plant") else normalized


def aggregate_sleep_capacity_objective(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    aggregate_terms = ("all ", "each ", "every ", "enough ", "adequate ", "capacity")
    sleep_terms = ("bed", "sleep")
    return any(term in normalized for term in aggregate_terms) and any(term in normalized for term in sleep_terms)


def generic_shelter_objective(text: str) -> bool:
    words = re.findall(r"[a-z0-9]+", text.lower())
    shelter_words = {"enclose", "enclosed", "enclosure", "roof", "roofed", "room", "rooms", "shelter", "shelters"}
    generic_words = shelter_words | {
        "a", "an", "and", "build", "complete", "create", "finish", "for", "indoor", "indoors", "make",
        "of", "safe", "starter", "temperature", "the", "to", "usable",
    }
    return bool(shelter_words.intersection(words)) and all(word in generic_words for word in words)


def room_is_completed_shelter(room: dict[str, Any] | None) -> bool:
    if not isinstance(room, dict):
        return False
    coverage = numeric(room.get("roofCoverage"))
    return (
        room.get("enclosed") is True
        and room.get("indoors") is True
        and room.get("usesOutdoorTemperature") is False
        and coverage >= 0.5
    )


def room_identity(room: dict[str, Any]) -> str:
    bounds = room.get("bounds") if isinstance(room.get("bounds"), dict) else {}
    return canonical_json({
        "bounds": bounds,
        "cellCount": integer(room.get("cellCount")),
        "roofedCellCount": integer(room.get("roofedCellCount")),
    })


def normalized_domain_text(item: dict[str, Any]) -> str:
    values = [item.get("objective"), item.get("nextAction"), item.get("next_action"), item.get("key")]
    values.extend(item.get("blockers", []) if isinstance(item.get("blockers"), list) else [])
    return " ".join(str(value or "") for value in values).lower()


def matching_domain_name(text: str, names: list[str]) -> str | None:
    normalized_text = normalize_identifier(text)
    generic = {"wall", "door", "floor", "bed", "building"}
    for name in sorted((item for item in names if item), key=len, reverse=True):
        normalized = normalize_identifier(name)
        if normalized and normalized not in generic and normalized in normalized_text:
            return name
    return None


def matching_domain_entry(text: str, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized_text = normalize_identifier(text)
    for entry in entries:
        candidates = (entry.get("defName"), entry.get("label"))
        if any(normalize_identifier(value) in normalized_text for value in candidates if normalize_identifier(value)):
            return entry
    return None


def normalize_identifier(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def numeric(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def project_tasks_by_id(handoff: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    result = {}
    for project in (handoff or {}).get("projects", []):
        if not isinstance(project, dict):
            continue
        for task in project.get("tasks", []):
            if isinstance(task, dict) and task.get("id"):
                result[str(task["id"])] = task
    return result


def project_task_fingerprint(task: dict[str, Any]) -> str:
    value = {
        "objective": short_text(task.get("objective"), 160),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "dependsOn": sorted(str(item) for item in task.get("dependsOn", [])),
        "blockers": sorted(short_text(item, 120) for item in task.get("blockers", [])),
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def project_task_categories(task: dict[str, Any]) -> set[str]:
    return loop_categories({
        "objective": task.get("objective"),
        "nextAction": " ".join(str(item) for item in task.get("blockers", [])),
    })


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
