"""Compact deterministic model context built from local authoritative state."""

from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any, Callable

from colony_state_query import ColonyStateQuery, compact_item, dict_list, select_fields
from context_telemetry import estimate_tokens, serialized_chars
from progress_tracking import build_progress_signals, build_stall_context
from risk_tracking import build_operational_risk_summary
from state_diff import StateDiff
from state_store import StateStore, snapshot_version
from strategic_projects import build_project_context


CONTEXT_VERSION = 1
DEFAULT_MAX_BOOTSTRAP_CHARS = 14_000
DEFAULT_MAX_BOOTSTRAP_PAWNS = 20
NUTRITION_PER_COLONIST_DAY = 1.6


class DecisionContextError(RuntimeError):
    pass


class DecisionContextBuilder:
    def __init__(
        self,
        state_store: StateStore,
        *,
        max_bootstrap_chars: int = DEFAULT_MAX_BOOTSTRAP_CHARS,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.state_store = state_store
        self.max_bootstrap_chars = max(4000, max_bootstrap_chars)
        self.log = logger or print
        self.query = ColonyStateQuery(state_store, max_section_chars=6000, max_entries=40)

    def build(self, trigger: dict[str, Any] | None = None) -> dict[str, Any]:
        state = self.state_store.get_current_state()
        if not isinstance(state, dict):
            raise DecisionContextError("Cannot build decision context without authoritative current state")
        delta = self.state_store.get_changes_since_last_decision()
        memory = self.state_store.get_memory()
        previous_decision = self.state_store.get_decision_handoff()
        progress = build_progress_signals(self.state_store.get_decision_baseline(), state)
        operational_risk = build_operational_risk_summary(
            self.state_store.get_decision_baseline(), state, self.state_store.get_risk_metadata()
        )
        strategic_projects = build_project_context(
            previous_decision, progress, self.state_store.get_stall_metadata(), state, operational_risk
        )
        context: dict[str, Any] = {
            "contextVersion": CONTEXT_VERSION,
            "bootstrap": bool(delta.get("bootstrapRequired")),
            "strategicMemory": memory or {},
            "currentSummary": build_current_summary(state),
            "changesSinceLastDecision": delta,
            "progressSinceLastDecision": progress,
            "operationalRisk": operational_risk,
            "strategicProjects": strategic_projects,
            "trigger": build_context_trigger(trigger, state, delta),
        }
        if previous_decision is not None:
            context["previousDecision"] = previous_decision
        stall_context = build_stall_context(
            self.state_store.get_stall_metadata(), previous_decision, progress
        )
        if stall_context is not None:
            context["stallRecovery"] = stall_context
        if context["bootstrap"]:
            context["bootstrapState"] = build_bootstrap_state(state)
            context = bound_bootstrap_context(context, self.max_bootstrap_chars)
            bootstrap = context.get("bootstrapState", {})
            self.log(
                "[BOOTSTRAP] "
                f"chars={serialized_chars(bootstrap)} estimatedTokens={estimate_tokens(serialized_chars(bootstrap))} "
                f"colonistsChars={serialized_chars(bootstrap.get('colonists', []))} "
                f"resourcesChars={serialized_chars(bootstrap.get('resources', {}))} "
                f"structuresChars={serialized_chars(bootstrap.get('structures', {}))} "
                f"mapChars={serialized_chars(bootstrap.get('mapOverview', {}))}"
            )
        full_chars = serialized_chars(state)
        compact_chars = serialized_chars(context)
        compression = (full_chars / compact_chars) if compact_chars else 0.0
        self.log(
            "[CONTEXT COMPARISON] "
            f"fullStateChars={full_chars} memoryChars={serialized_chars(context.get('strategicMemory', {}))} "
            f"summaryChars={serialized_chars(context.get('currentSummary', {}))} "
            f"deltaChars={serialized_chars(context.get('changesSinceLastDecision', {}))} "
            f"progressChars={serialized_chars(context.get('progressSinceLastDecision', {}))} "
            f"stallChars={serialized_chars(context.get('stallRecovery', {}))} "
            f"triggerChars={serialized_chars(context.get('trigger', {}))} "
            f"decisionHandoffChars={serialized_chars(context.get('previousDecision')) if 'previousDecision' in context else 0} "
            f"compactDynamicChars={compact_chars} dynamicCompression={compression:.1f}x fullStateSent=false"
        )
        return context

    def build_post_tool_context(
        self,
        before_state: dict[str, Any] | None,
        *,
        stale: bool = False,
        note: str | None = None,
    ) -> dict[str, Any]:
        current = self.state_store.get_current_state()
        if not isinstance(current, dict):
            raise DecisionContextError("No authoritative state is available after the tool round")
        progress = build_progress_signals(before_state, current)
        operational_risk = build_operational_risk_summary(
            before_state, current, self.state_store.get_risk_metadata()
        )
        result: dict[str, Any] = {
            "contextVersion": CONTEXT_VERSION,
            "postToolState": True,
            "authoritative": not stale,
            "currentSummary": build_current_summary(current),
            "changesSinceToolRound": StateDiff.compare(before_state, current),
            "progressSinceToolRound": progress,
            "operationalRisk": operational_risk,
            "strategicProjects": build_project_context(
                self.state_store.get_decision_handoff(),
                progress,
                self.state_store.get_stall_metadata(),
                current,
                operational_risk,
            ),
        }
        if note:
            result["note"] = note
        return result


def build_current_summary(state: dict[str, Any]) -> dict[str, Any]:
    snapshot = state.get("snapshot") if isinstance(state.get("snapshot"), dict) else {}
    game = state.get("game") if isinstance(state.get("game"), dict) else {}
    colony = state.get("colony") if isinstance(state.get("colony"), dict) else {}
    colonists = dict_list(state.get("colonists"))
    threats = dict_list(state.get("threats"))
    research = state.get("research") if isinstance(state.get("research"), dict) else {}
    current_research = research.get("current") if isinstance(research.get("current"), dict) else None

    result: dict[str, Any] = {
        "snapshotVersion": snapshot.get("version"),
        "ticksGame": game.get("ticksGame", snapshot.get("ticksGame")),
        "game": {"loaded": game.get("loaded"), "paused": game.get("paused"), "speed": game.get("speed")},
        "colony": {
            "colonists": colony.get("colonistCount", len(colonists)),
            "prisoners": colony.get("prisonerCount"),
            "animals": colony.get("animalCount"),
        },
        "threats": {"status": "active" if threats else "none", "active": len(threats)},
        "health": health_summary(colonists),
        "food": food_summary(state, len(colonists)),
        "research": {
            "active": current_research.get("defName") if current_research else None,
            "label": current_research.get("label") if current_research else None,
        },
    }

    wealth = wealth_summary(state)
    if wealth is not None:
        result["wealth"] = wealth

    awareness = awareness_summary(state)
    if awareness is not None:
        result["awareness"] = awareness
    labor = labor_summary(state)
    if labor is not None:
        result["labor"] = labor

    power = power_summary(state)
    if power is not None:
        result["power"] = power
    unarmed = sum(1 for pawn in colonists if not isinstance(pawn.get("primaryEquipment"), dict))
    result["equipment"] = {"unarmedColonists": unarmed}
    missing_beds = missing_bed_count(state, len(colonists))
    if missing_beds is not None:
        result["shelter"] = {"missingBeds": missing_beds, "status": "adequate" if missing_beds == 0 else "inadequate"}
    map_state = state.get("map") if isinstance(state.get("map"), dict) else {}
    environment = map_state.get("environment") if isinstance(map_state.get("environment"), dict) else {}
    if environment:
        result["environment"] = select_fields(environment, ("outdoorTemperature", "season", "growingSeason", "weather"))
    return result


def awareness_summary(state: dict[str, Any]) -> dict[str, Any] | None:
    awareness = state.get("awareness") if isinstance(state.get("awareness"), dict) else None
    if awareness is None:
        return None
    alerts = dict_list(awareness.get("activeAlerts"))
    letters = dict_list(awareness.get("activeLetters"))
    recent = dict_list(awareness.get("recentEvents"))
    important = [
        select_fields(item, ("id", "type", "severity", "title", "text", "ticksGame"))
        for item in (alerts + letters + recent)
        if str(item.get("severity") or "").lower() in ("high", "critical", "medium")
    ]
    return {
        "status": "attentionRequired" if alerts or letters or important else "clear",
        "activeAlerts": len(alerts),
        "activeLetters": len(letters),
        "important": important[:8],
    }


def labor_summary(state: dict[str, Any]) -> dict[str, Any] | None:
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    labor = operations.get("labor") if isinstance(operations.get("labor"), dict) else None
    if labor is None:
        return None
    return {
        "idleColonists": labor.get("idleColonistCount"),
        "capableIdleColonists": labor.get("capableIdleColonistCount"),
        "capableIdlePawnIds": [
            str(item.get("id"))
            for item in dict_list(labor.get("capableIdleColonists"))[:20]
            if item.get("id")
        ],
        "pendingWork": copy.deepcopy(labor.get("pendingWork")),
        "obviousBlockers": copy.deepcopy(dict_list(labor.get("obviousBlockers"))[:12]),
    }


def build_bootstrap_state(state: dict[str, Any]) -> dict[str, Any]:
    colonists = [bootstrap_pawn(pawn) for pawn in dict_list(state.get("colonists"))[:DEFAULT_MAX_BOOTSTRAP_PAWNS]]
    resources = bootstrap_resources(state)
    research = state.get("research") if isinstance(state.get("research"), dict) else {}
    available_research = [select_fields(item, ("defName", "label", "progress", "cost")) for item in dict_list(research.get("available"))[:16]]
    threats = [
        select_fields(item, ("id", "type", "defName", "label", "faction", "dangerReason", "position", "downed", "weapon"))
        for item in dict_list(state.get("threats"))[:30]
    ]
    return {
        "colonists": colonists,
        "colonistsTruncated": len(dict_list(state.get("colonists"))) > len(colonists),
        "resources": resources,
        "research": {
            "current": copy.deepcopy(research.get("current")),
            "availableCount": len(dict_list(research.get("available"))),
            "availableSample": available_research,
            "completedCount": len(dict_list(research.get("completed"))),
            "completedSample": [
                select_fields(item, ("defName", "label"))
                for item in dict_list(research.get("completed"))[:20]
            ],
        },
        "immediateThreats": threats,
        "threatsTruncated": len(dict_list(state.get("threats"))) > len(threats),
        "structures": bootstrap_structures(state),
        "mapOverview": bootstrap_map(state),
    }


def bootstrap_pawn(pawn: dict[str, Any]) -> dict[str, Any]:
    skills = [select_fields(item, ("defName", "level", "passion")) for item in dict_list(pawn.get("skills"))]
    skills.sort(key=lambda item: (-safe_int(item.get("level")), str(item.get("defName") or "")))
    key_skills = [item for item in skills if item.get("passion") not in (None, "None") or safe_int(item.get("level")) >= 7][:10]
    work = dict_list(pawn.get("work"))
    enabled = [select_fields(item, ("defName", "priority")) for item in work if item.get("capable") is True and safe_int(item.get("priority")) > 0][:16]
    incapable = [select_fields(item, ("defName", "disabledReason")) for item in work if item.get("capable") is False or item.get("disabled") is True][:12]
    health = pawn.get("health") if isinstance(pawn.get("health"), dict) else {}
    critical_health = None
    if health.get("summary") not in (None, "healthy") or health.get("downed") or health.get("dead"):
        critical_health = select_fields(health, ("summary", "downed", "dead", "bleedingRate", "pain"))
        hediffs = [select_fields(item, ("defName", "label", "severity")) for item in dict_list(health.get("hediffs"))[:8]]
        if critical_health is not None and hediffs:
            critical_health["hediffs"] = hediffs
    return {
        "id": pawn.get("id"),
        "name": pawn.get("name"),
        "kindDef": pawn.get("kindDef"),
        "keySkills": key_skills,
        "enabledWork": enabled,
        "incapableWork": incapable,
        "primaryEquipment": compact_item(pawn.get("primaryEquipment")),
        "criticalHealth": critical_health,
    }


def bootstrap_resources(state: dict[str, Any]) -> dict[str, Any]:
    resources = state.get("resources") if isinstance(state.get("resources"), dict) else {}
    available = resources.get("available") if isinstance(resources.get("available"), dict) else resources
    forbidden = resources.get("forbidden") if isinstance(resources.get("forbidden"), dict) else {}
    keys = (
        "silver", "wood", "steel", "plasteel", "components", "advancedComponents",
        "medicine", "industrialMedicine", "glitterworldMedicine", "food",
    )
    known = set(keys)
    other_available = {
        str(key): copy.deepcopy(available[key])
        for key in sorted(available)
        if key not in known and isinstance(available[key], (int, float)) and available[key] != 0
    }
    result = {
        "available": {key: copy.deepcopy(available[key]) for key in keys if key in available},
        "forbidden": {key: copy.deepcopy(forbidden[key]) for key in keys if key in forbidden},
    }
    if other_available:
        result["otherAvailable"] = dict(list(other_available.items())[:20])
    return result


def bootstrap_structures(state: dict[str, Any]) -> dict[str, Any]:
    buildings = dict_list(state.get("buildings"))
    counts = Counter(str(item.get("defName") or item.get("label") or item.get("type") or "unknown") for item in buildings)
    details = []
    important_words = ("bed", "bench", "table", "stove", "generator", "battery", "cooler", "heater", "door")
    for item in sorted(buildings, key=lambda entry: (str(entry.get("defName") or ""), str(entry.get("id") or ""))):
        identity = str(item.get("defName") or item.get("label") or "").lower()
        if item.get("type") in ("blueprint", "frame") or any(word in identity for word in important_words):
            details.append(select_fields(item, ("id", "type", "defName", "label", "position", "rotation", "stuffDef", "powered")))
        if len(details) >= 40:
            break
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    beds = dict_list(operations.get("beds"))
    tables = dict_list(operations.get("worktables"))
    map_state = state.get("map") if isinstance(state.get("map"), dict) else {}
    zones = dict_list(map_state.get("zones"))
    return {
        "countsByDef": [{"defName": key, "count": count} for key, count in sorted(counts.items())[:60]],
        "importantEntries": details,
        "beds": {"total": len(beds), "colonistUsable": sum(1 for bed in beds if not bed.get("medical") and not bed.get("forPrisoners"))},
        "worktables": [select_fields(item, ("id", "defName", "label", "position", "operational")) for item in tables[:20]],
        "zones": [select_fields(item, (
            "id", "type", "label", "cellCount", "bounds", "plantDef", "priority", "preset",
            "plantedCells", "unsownEligibleCells", "growingCells", "harvestableCells",
            "plantingComplete", "growingState",
        )) for item in zones[:30]],
        "power": power_summary(state),
    }


def bootstrap_map(state: dict[str, Any]) -> dict[str, Any]:
    map_state = state.get("map") if isinstance(state.get("map"), dict) else {}
    return {
        "id": map_state.get("id"),
        "width": map_state.get("width"),
        "height": map_state.get("height"),
        "colonyCenter": copy.deepcopy(map_state.get("colonyCenter")),
        "homeAreaBounds": copy.deepcopy(map_state.get("homeAreaBounds")),
        "environment": copy.deepcopy(map_state.get("environment")),
        "pointsOfInterest": [select_fields(item, ("id", "type", "defName", "label", "position")) for item in dict_list(map_state.get("pointsOfInterest"))[:20]],
    }


def bound_bootstrap_context(context: dict[str, Any], max_chars: int) -> dict[str, Any]:
    result = copy.deepcopy(context)
    bootstrap = result.get("bootstrapState")
    if not isinstance(bootstrap, dict) or serialized_chars(bootstrap) <= max_chars:
        return result
    bootstrap["truncated"] = True
    trim_order = (
        ("research", "availableSample"),
        ("structures", "countsByDef"),
        ("structures", "importantEntries"),
        ("structures", "zones"),
        ("structures", "worktables"),
        ("mapOverview", "pointsOfInterest"),
        ("immediateThreats",),
        ("colonists",),
    )
    for path in trim_order:
        target = nested_value(bootstrap, path)
        while isinstance(target, list) and target and serialized_chars(bootstrap) > max_chars:
            target.pop()
        if serialized_chars(bootstrap) <= max_chars:
            break
    if serialized_chars(bootstrap) > max_chars:
        result["bootstrapState"] = {
            "truncated": True,
            "colonists": bootstrap.get("colonists", [])[:3],
            "resources": bootstrap.get("resources", {}),
            "immediateThreats": bootstrap.get("immediateThreats", [])[:10],
            "mapOverview": bootstrap.get("mapOverview", {}),
        }
    return result


def health_summary(colonists: list[dict[str, Any]]) -> dict[str, Any]:
    downed = 0
    injured = 0
    emergency = False
    urgent_conditions: list[dict[str, Any]] = []
    for pawn in colonists:
        health = pawn.get("health") if isinstance(pawn.get("health"), dict) else {}
        if health.get("downed"):
            downed += 1
        if health.get("summary") not in (None, "healthy"):
            injured += 1
        if health.get("downed") or health.get("dead") or safe_float(health.get("bleedingRate")) > 0:
            emergency = True
        for hediff in dict_list(health.get("hediffs")):
            def_name = str(hediff.get("defName") or "")
            if (
                any(marker in def_name.lower() for marker in ("heatstroke", "hypothermia", "bloodloss", "infection"))
                and safe_float(hediff.get("severity")) >= 0.4
            ):
                urgent_conditions.append({
                    "pawnId": pawn.get("id"),
                    "defName": hediff.get("defName"),
                    "label": hediff.get("label"),
                    "severity": hediff.get("severity"),
                })
    return {
        "status": "emergency" if emergency else ("injured" if injured else "stable"),
        "emergency": emergency,
        "downedColonists": downed,
        "injuredColonists": injured,
        "urgentConditions": urgent_conditions[:12],
    }


def food_summary(state: dict[str, Any], colonist_count: int) -> dict[str, Any]:
    resources = state.get("resources") if isinstance(state.get("resources"), dict) else {}
    available = resources.get("available") if isinstance(resources.get("available"), dict) else resources
    food = available.get("food") if isinstance(available.get("food"), dict) else {}
    nutrition = safe_float(food.get("totalNutrition"))
    meals = safe_int(food.get("meals"))
    divisor = max(1, colonist_count)
    per_colonist = nutrition / divisor
    if per_colonist < 2:
        status = "critical"
    elif per_colonist < 6:
        status = "low"
    elif per_colonist < 20:
        status = "adequate"
    else:
        status = "abundant"
    estimated_days = None
    if colonist_count > 0:
        estimated_days = round(nutrition / (colonist_count * NUTRITION_PER_COLONIST_DAY), 1)
    return {"status": status, "meals": meals, "estimatedFoodDays": estimated_days}


def wealth_summary(state: dict[str, Any]) -> dict[str, Any] | None:
    colony = state.get("colony") if isinstance(state.get("colony"), dict) else {}
    wealth = colony.get("wealth") if isinstance(colony.get("wealth"), dict) else None
    if wealth is None:
        return None
    return select_fields(wealth, ("total", "itemValue", "buildingValue", "pawnValue"))


def power_summary(state: dict[str, Any]) -> dict[str, Any] | None:
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    power = operations.get("power") if isinstance(operations.get("power"), dict) else None
    if power is None:
        return None
    networks = dict_list(power.get("networks"))
    if not networks:
        return {"status": "none", "networks": 0, "unpoweredBuildings": len(dict_list(power.get("unpoweredBuildings")))}
    generation = sum(safe_float(item.get("generationWatts")) for item in networks)
    consumption = sum(safe_float(item.get("consumptionWatts")) for item in networks)
    net = sum(safe_float(item.get("netWatts")) for item in networks)
    stored = sum(safe_float(item.get("storedEnergyWd")) for item in networks)
    unpowered = len(dict_list(power.get("unpoweredBuildings")))
    if net < 0 or unpowered:
        status = "deficit"
    elif generation <= 0 and consumption > 0:
        status = "unstable"
    elif net > 100:
        status = "surplus"
    else:
        status = "adequate"
    return {"status": status, "networks": len(networks), "netWatts": round(net, 1), "storedEnergyWd": round(stored, 1), "unpoweredBuildings": unpowered}


def missing_bed_count(state: dict[str, Any], colonist_count: int) -> int | None:
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    if "beds" not in operations:
        return None
    beds = dict_list(operations.get("beds"))
    usable = sum(1 for bed in beds if not bed.get("medical") and not bed.get("forPrisoners"))
    return max(0, colonist_count - usable)


def normalize_trigger(trigger: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(trigger, dict):
        return {"type": "manualCycle"}
    trigger_type = str(trigger.get("type") or "manualCycle")
    result = {"type": trigger_type[:80]}
    for key in sorted(trigger):
        if key == "type":
            continue
        value = trigger[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)[:80]] = value[:500] if isinstance(value, str) else value
        elif isinstance(value, list):
            primitive = [item for item in value[:20] if isinstance(item, (str, int, float, bool))]
            if primitive:
                result[str(key)[:80]] = primitive
    return result


def build_context_trigger(trigger: dict[str, Any] | None, state: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    result = normalize_trigger(trigger)
    summary = awareness_summary(state)
    changes = delta.get("changes") if isinstance(delta.get("changes"), dict) else {}
    awareness_changes = changes.get("awareness") if isinstance(changes.get("awareness"), dict) else None
    if summary is not None and (summary.get("status") == "attentionRequired" or awareness_changes):
        result["playerAwareness"] = {
            "status": summary.get("status"),
            "important": copy.deepcopy(summary.get("important", []))[:4],
            "changed": bool(awareness_changes),
        }
    return result


def nested_value(value: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def serialize_context(context: dict[str, Any]) -> str:
    return json.dumps(context, separators=(",", ":"), ensure_ascii=True, sort_keys=True)
