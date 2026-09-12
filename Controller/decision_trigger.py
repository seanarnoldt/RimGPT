"""Deterministic, side-effect-free decision trigger evaluation."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Any

from progress_tracking import authoritative_completion_for, build_progress_signals
from risk_tracking import TRIAGE_ORDER, build_operational_risk_summary
from state_store import (
    identity_from_state,
    is_safe_snapshot_stream_reset,
    snapshot_ticks,
    snapshot_version,
    state_schema_version,
)
from strategic_projects import build_project_context


DEFAULT_REVIEW_INTERVAL_TICKS = 30_000
DEFAULT_IDLE_PERSISTENCE_TICKS = 2_500
MAX_TRIGGER_FINGERPRINTS = 64
KIND_ORDER = {
    "colonist_downed": 0,
    "risk_escalation": 1,
    "new_threat": 2,
    "threat_increase": 3,
    "project_task_satisfied": 10,
    "blocker_cleared": 11,
    "project_task_stalled": 12,
    "construction_blocked": 13,
    "research_complete": 14,
    "construction_complete": 15,
    "productive_idleness": 20,
}


@dataclass(frozen=True, slots=True)
class DecisionTrigger:
    should_trigger: bool
    kind: str
    priority: str
    reason: str
    fingerprint: str | None = None
    ticks_game: int | None = None
    evidence: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class TriggerContext:
    handoff: dict[str, Any] | None = None
    stall_metadata: dict[str, Any] | None = None
    risk_metadata: dict[str, Any] | None = None


class TriggerEvaluator:
    """Select one meaningful transition and suppress duplicate conditions."""

    def __init__(
        self,
        review_interval_ticks: int = DEFAULT_REVIEW_INTERVAL_TICKS,
        idle_persistence_ticks: int = DEFAULT_IDLE_PERSISTENCE_TICKS,
        max_fingerprints: int = MAX_TRIGGER_FINGERPRINTS,
    ) -> None:
        if review_interval_ticks <= 0:
            raise ValueError("review_interval_ticks must be positive")
        if max_fingerprints <= 0:
            raise ValueError("max_fingerprints must be positive")
        if idle_persistence_ticks <= 0:
            raise ValueError("idle_persistence_ticks must be positive")
        self.review_interval_ticks = review_interval_ticks
        self.idle_persistence_ticks = idle_persistence_ticks
        self.max_fingerprints = max_fingerprints
        self._fingerprints: deque[str] = deque()
        self._fingerprint_set: set[str] = set()
        self._last_review_tick: int | None = None
        self._latest_tick: int | None = None
        self._idle_since_tick: int | None = None
        self._idle_signature: tuple[str, ...] = ()

    @property
    def fingerprint_count(self) -> int:
        return len(self._fingerprints)

    def reset(self, ticks_game: int | None = None) -> None:
        self._fingerprints.clear()
        self._fingerprint_set.clear()
        self._last_review_tick = ticks_game
        self._idle_since_tick = None
        self._idle_signature = ()

    def acknowledge_review(self, ticks_game: int | None = None) -> None:
        """Start the next review interval after a future scheduler handles one."""
        acknowledged = ticks_game if ticks_game is not None else self._latest_tick
        if acknowledged is not None:
            self._last_review_tick = acknowledged

    def acknowledge_trigger(self, decision: DecisionTrigger) -> None:
        """Acknowledge scheduler handling without persisting observer state."""
        if decision.kind == "periodic_review":
            self.acknowledge_review(decision.ticks_game)
        if decision.kind == "productive_idleness":
            if decision.fingerprint:
                self._fingerprint_set.discard(decision.fingerprint)
                try:
                    self._fingerprints.remove(decision.fingerprint)
                except ValueError:
                    pass
            self._idle_since_tick = self._latest_tick

    def evaluate(
        self,
        previous: dict[str, Any] | None,
        current: dict[str, Any],
        context: TriggerContext | None = None,
    ) -> DecisionTrigger:
        ticks = state_ticks(current)
        self._latest_tick = ticks
        if not isinstance(previous, dict):
            self.reset(ticks)
            self._track_productive_idleness(current, emit=False)
            return no_trigger("Initial authoritative observation established", ticks)
        if not compatible_observation_stream(previous, current):
            self.reset(ticks)
            self._track_productive_idleness(current, emit=False)
            return no_trigger("Authoritative observation stream changed; baseline re-established", ticks)

        context = context or TriggerContext()
        progress = build_progress_signals(previous, current)
        candidates = self._urgent_candidates(previous, current, context)
        candidates.extend(self._normal_candidates(previous, current, context, progress))
        idle_candidate = self._track_productive_idleness(current, emit=True)
        if idle_candidate is not None:
            candidates.append(idle_candidate)
        candidates.sort(key=lambda item: (
            0 if item.priority == "urgent" else 1,
            KIND_ORDER.get(item.kind, 50),
            item.kind,
            item.fingerprint or "",
        ))
        for candidate in candidates:
            if candidate.fingerprint not in self._fingerprint_set:
                self._remember(candidate.fingerprint)
                return candidate

        periodic = self._periodic_review(current)
        if periodic is not None and periodic.fingerprint not in self._fingerprint_set:
            self._remember(periodic.fingerprint)
            return periodic
        return no_trigger("No meaningful authoritative state transition", ticks)

    def _track_productive_idleness(
        self, current: dict[str, Any], *, emit: bool
    ) -> DecisionTrigger | None:
        ticks = state_ticks(current)
        labor_state = labor(current)
        count = int_value(labor_state.get("capableIdleColonistCount"))
        ids = tuple(sorted(
            str(item.get("id"))
            for item in dict_list(labor_state.get("capableIdleColonists"))
            if item.get("id")
        ))
        signature = ids or tuple(f"count:{index}" for index in range(count))
        if count <= 0 or ticks is None:
            self._idle_since_tick = None
            self._idle_signature = ()
            return None
        if signature != self._idle_signature or self._idle_since_tick is None or ticks < self._idle_since_tick:
            self._idle_signature = signature
            self._idle_since_tick = ticks
            return None
        if not emit or ticks - self._idle_since_tick < self.idle_persistence_ticks:
            return None
        evidence = {"capableIdleColonists": count, "capableIdlePawnIds": list(ids)}
        return trigger(
            "productive_idleness",
            "normal",
            "Capable colonists have remained idle; current strategy may not provide enough useful work",
            ticks,
            evidence,
        )

    def _urgent_candidates(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
        context: TriggerContext,
    ) -> list[DecisionTrigger]:
        ticks = state_ticks(current)
        result: list[DecisionTrigger] = []
        old_threats, new_threats = by_stable_id(previous.get("threats")), by_stable_id(current.get("threats"))
        added = sorted(set(new_threats) - set(old_threats))
        for thing_id in added:
            threat = new_threats[thing_id]
            label = threat.get("label") or threat.get("defName") or thing_id
            reason = threat.get("dangerReason") or "active visible threat"
            result.append(trigger("new_threat", "urgent", f"New visible threat: {label} ({reason})", ticks, {
                "id": thing_id, "dangerReason": reason, "observedAtTick": ticks,
            }))
        if not added and len(new_threats) > len(old_threats):
            result.append(trigger(
                "threat_increase", "urgent",
                f"Visible active threats increased from {len(old_threats)} to {len(new_threats)}",
                ticks, {"from": len(old_threats), "to": len(new_threats)},
            ))

        old_pawns, new_pawns = by_stable_id(previous.get("colonists")), by_stable_id(current.get("colonists"))
        for pawn_id in sorted(set(old_pawns) & set(new_pawns)):
            old_health, new_health = health(old_pawns[pawn_id]), health(new_pawns[pawn_id])
            if not bool(old_health.get("downed")) and bool(new_health.get("downed")):
                result.append(trigger("colonist_downed", "urgent", f"Colonist newly downed: {pawn_name(new_pawns[pawn_id])}", ticks, {"pawnId": pawn_id}))

        old_risk = build_operational_risk_summary(previous, previous, context.risk_metadata)
        new_risk = build_operational_risk_summary(previous, current, context.risk_metadata)
        old_level = TRIAGE_ORDER.get(str(old_risk.get("overallTriage")), 0)
        new_level = TRIAGE_ORDER.get(str(new_risk.get("overallTriage")), 0)
        if new_level >= TRIAGE_ORDER["urgent"] and new_level > old_level:
            item = first_urgent_risk(new_risk)
            detail = f" for {item.get('pawnId')}: {item.get('condition')}" if item else ""
            result.append(trigger(
                "risk_escalation", "urgent",
                f"Operational risk became {new_risk.get('overallTriage')}{detail}", ticks,
                {"triage": new_risk.get("overallTriage"), "item": risk_identity(item)},
            ))
        else:
            old_items = {risk_key(item): item for item in dict_list(old_risk.get("items"))}
            for item in dict_list(new_risk.get("items")):
                if item.get("triage") not in ("urgent", "critical"):
                    continue
                old_item = old_items.get(risk_key(item))
                if old_item is None:
                    continue
                severity_increase = number(item.get("severity")) - number(old_item.get("severity"))
                triage_increase = TRIAGE_ORDER.get(str(item.get("triage")), 0) > TRIAGE_ORDER.get(str(old_item.get("triage")), 0)
                if severity_increase < 0.1 and not triage_increase:
                    continue
                result.append(trigger(
                    "risk_escalation", "urgent",
                    f"Operational risk materially worsened for {item.get('pawnId')}: {item.get('condition')}",
                    ticks,
                    {"item": risk_identity(item), "severityBand": round(number(item.get("severity")), 1)},
                ))
        return result

    def _normal_candidates(
        self,
        previous: dict[str, Any],
        current: dict[str, Any],
        context: TriggerContext,
        progress: dict[str, Any],
    ) -> list[DecisionTrigger]:
        ticks = state_ticks(current)
        result: list[DecisionTrigger] = []
        for signal in dict_list(progress.get("signals")):
            signal_type = signal.get("type")
            if signal_type == "researchCompleted":
                name = str(signal.get("defName") or "unknown research")
                result.append(trigger("research_complete", "normal", f"Research completed: {name}", ticks, signal))
            elif signal_type == "blockerCleared":
                result.append(trigger("blocker_cleared", "normal", "A known work blocker cleared", ticks, signal))
            elif signal_type == "constructionAdvanced" and (signal.get("to") or {}).get("stage") == "building":
                name = str(signal.get("defName") or "construction")
                result.append(trigger("construction_complete", "normal", f"Construction completed: {name}", ticks, signal))
            elif signal_type == "designationCountChanged" and int_value(signal.get("to")) < int_value(signal.get("from")):
                result.append(trigger("designation_complete", "normal", "Designated work completed or disappeared", ticks, signal))
            elif signal_type == "growingZoneAdvanced":
                before_phase = str((signal.get("from") or {}).get("state") or "")
                after_phase = str((signal.get("to") or {}).get("state") or "")
                if signal.get("plantingComplete") is True and before_phase not in ("planted", "harvestable"):
                    result.append(trigger("sowing_complete", "normal", f"Sowing completed in {signal.get('zoneId')}", ticks, signal))
                if after_phase == "harvestable" and before_phase != "harvestable":
                    result.append(trigger("crops_harvestable", "normal", f"Crops became harvestable in {signal.get('zoneId')}", ticks, signal))

        old_colonists, new_colonists = by_stable_id(previous.get("colonists")), by_stable_id(current.get("colonists"))
        joined, left = sorted(set(new_colonists) - set(old_colonists)), sorted(set(old_colonists) - set(new_colonists))
        if joined or left:
            result.append(trigger(
                "colony_membership_changed", "normal",
                f"Colony membership changed: {len(joined)} joined, {len(left)} left", ticks,
                {"joined": joined, "left": left},
            ))

        awareness = new_awareness_entries(previous, current)
        if awareness:
            entry = awareness[0]
            label = entry.get("title") or entry.get("text") or entry.get("type") or "important event"
            result.append(trigger("important_awareness", "normal", f"New player-visible event: {label}", ticks, {
                "id": entry.get("id"), "type": entry.get("type"), "severity": entry.get("severity"),
            }))

        deterioration = resource_deterioration(previous, current)
        if deterioration is not None:
            result.append(trigger("resource_deterioration", "normal", deterioration[0], ticks, deterioration[1]))

        blockage = new_construction_blocker(previous, current)
        if blockage is not None:
            result.append(trigger("construction_blocked", "normal", "New construction blocker detected", ticks, blockage))

        project_candidates = project_transition_candidates(previous, current, context, progress, ticks)
        result.extend(project_candidates)
        return result

    def _periodic_review(self, current: dict[str, Any]) -> DecisionTrigger | None:
        ticks = state_ticks(current)
        if ticks is None:
            return None
        game = current.get("game") if isinstance(current.get("game"), dict) else {}
        if game.get("paused") is True:
            return None
        if self._last_review_tick is None or ticks < self._last_review_tick:
            self._last_review_tick = ticks
            return None
        due_tick = self._last_review_tick + self.review_interval_ticks
        if ticks < due_tick:
            return None
        return trigger(
            "periodic_review", "normal", "Periodic strategic review is due", ticks,
            {"dueTick": due_tick},
        )

    def _remember(self, fingerprint: str | None) -> None:
        if not fingerprint or fingerprint in self._fingerprint_set:
            return
        while len(self._fingerprints) >= self.max_fingerprints:
            self._fingerprint_set.discard(self._fingerprints.popleft())
        self._fingerprints.append(fingerprint)
        self._fingerprint_set.add(fingerprint)


def project_transition_candidates(
    previous: dict[str, Any],
    current: dict[str, Any],
    context: TriggerContext,
    progress: dict[str, Any],
    ticks: int | None,
) -> list[DecisionTrigger]:
    handoff = context.handoff
    if not isinstance(handoff, dict):
        return []
    result: list[DecisionTrigger] = []
    before_progress = build_progress_signals(previous, previous)
    for project in dict_list(handoff.get("projects")):
        tasks = {str(item.get("id")): item for item in dict_list(project.get("tasks")) if item.get("id")}
        for task_id, task in sorted(tasks.items()):
            if task.get("status") in ("completed", "background") or task.get("mode") == "background":
                continue
            before_evidence = authoritative_completion_for(task, before_progress)
            after_evidence = authoritative_completion_for(task, progress)
            if not before_evidence and after_evidence:
                dependents = sorted(
                    other_id for other_id, other in tasks.items()
                    if task_id in [str(value) for value in other.get("dependsOn", [])]
                )
                result.append(trigger(
                    "project_task_satisfied", "normal",
                    f"Authoritative state satisfies project task {task_id}" + (f" and may unlock {', '.join(dependents)}" if dependents else ""),
                    ticks, {"taskId": task_id, "evidence": after_evidence, "dependents": dependents},
                ))

    risk = build_operational_risk_summary(previous, current, context.risk_metadata)
    project_context = build_project_context(handoff, progress, context.stall_metadata, current, risk)
    for project in dict_list(project_context.get("projects")):
        for attention in dict_list(project.get("attentionTasks")):
            if attention.get("reason") != "stalled":
                continue
            task_id = str(attention.get("taskId") or "")
            result.append(trigger(
                "project_task_stalled", "normal", f"Project task is genuinely stalled: {task_id}", ticks,
                {"projectId": project.get("id"), "taskId": task_id},
            ))
    continuity = project_context.get("workContinuity") if isinstance(project_context.get("workContinuity"), dict) else {}
    if int_value(continuity.get("capableIdleColonists")) > 0 and int_value(continuity.get("availableProjectTasks")) > 0:
        available = sorted(
            str(task_id)
            for project in dict_list(project_context.get("projects"))
            for task_id in project.get("availableTaskIds", [])
        )
        result.append(trigger(
            "idle_actionable_work", "normal", "Capable colonists are idle while project work is actionable", ticks,
            {"availableTaskIds": available, "idle": int_value(continuity.get("capableIdleColonists"))},
        ))
    return result


def compatible_observation_stream(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    old_identity, new_identity = identity_from_state(previous), identity_from_state(current)
    if old_identity is None or new_identity is None or old_identity.key != new_identity.key:
        return False
    if state_schema_version(previous) != state_schema_version(current):
        return False
    old_game = previous.get("game") if isinstance(previous.get("game"), dict) else {}
    new_game = current.get("game") if isinstance(current.get("game"), dict) else {}
    if old_game.get("currentMapId") != new_game.get("currentMapId"):
        return False
    old_ticks, new_ticks = state_ticks(previous), state_ticks(current)
    if old_ticks is not None and new_ticks is not None and new_ticks < old_ticks:
        return False
    old_version, new_version = snapshot_version(previous), snapshot_version(current)
    if old_version is not None and new_version is not None and new_version < old_version:
        return is_safe_snapshot_stream_reset(previous, current)
    return True


def resource_deterioration(previous: dict[str, Any], current: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    old = available_resources(previous)
    new = available_resources(current)
    old_food, new_food = food_nutrition(old), food_nutrition(new)
    for threshold in (20.0, 10.0, 5.0):
        if old_food >= threshold > new_food:
            return (f"Available food nutrition fell below {threshold:g}", {"resource": "foodNutrition", "threshold": threshold, "from": old_food, "to": new_food})
    for key, minimum_drop in (("wood", 25.0), ("steel", 50.0), ("components", 3.0), ("medicine", 3.0)):
        old_value, new_value = number(old.get(key)), number(new.get(key))
        if old_value > 0 and new_value <= old_value * 0.5 and old_value - new_value >= minimum_drop:
            return (f"Available {key} materially decreased", {"resource": key, "from": old_value, "to": new_value})
    return None


def new_construction_blocker(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any] | None:
    old_labor, new_labor = labor(previous), labor(current)
    pending = new_labor.get("pendingWork") if isinstance(new_labor.get("pendingWork"), dict) else {}
    if int_value(pending.get("blueprints")) + int_value(pending.get("frames")) <= 0:
        return None
    old = {canonical(item): item for item in dict_list(old_labor.get("obviousBlockers"))}
    new = {canonical(item): item for item in dict_list(new_labor.get("obviousBlockers"))}
    for key in sorted(set(new) - set(old)):
        item = new[key]
        category = str(item.get("category") or "").lower()
        text = canonical(item).lower()
        if category == "construction" or any(word in text for word in ("construct", "material", "builder")):
            return item
    return None


def new_awareness_entries(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    old = previous.get("awareness") if isinstance(previous.get("awareness"), dict) else {}
    new = current.get("awareness") if isinstance(current.get("awareness"), dict) else {}
    result = []
    for field in ("activeAlerts", "activeLetters", "recentEvents"):
        old_items, new_items = by_stable_id(old.get(field)), by_stable_id(new.get(field))
        for item_id in sorted(set(new_items) - set(old_items)):
            item = new_items[item_id]
            severity = str(item.get("severity") or "").lower()
            if field != "recentEvents" or severity in ("medium", "high", "critical"):
                result.append(item)
    return result


def trigger(kind: str, priority: str, reason: str, ticks: int | None, evidence: Any) -> DecisionTrigger:
    canonical = json.dumps({"kind": kind, "evidence": evidence}, sort_keys=True, separators=(",", ":"), default=str)
    fingerprint = kind + ":" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    retained = copy.deepcopy(evidence) if isinstance(evidence, dict) else None
    return DecisionTrigger(True, kind, priority, reason, fingerprint, ticks, retained)


def no_trigger(reason: str, ticks: int | None) -> DecisionTrigger:
    return DecisionTrigger(False, "none", "none", reason, None, ticks, None)


def state_ticks(state: dict[str, Any]) -> int | None:
    value = snapshot_ticks(state)
    if value is not None:
        return value
    game = state.get("game") if isinstance(state.get("game"), dict) else {}
    return int_value_or_none(game.get("ticksGame"))


def by_stable_id(value: Any) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in dict_list(value) if item.get("id")}


def dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def health(pawn: dict[str, Any]) -> dict[str, Any]:
    return pawn.get("health") if isinstance(pawn.get("health"), dict) else {}


def pawn_name(pawn: dict[str, Any]) -> str:
    return str(pawn.get("name") or pawn.get("id") or "unknown pawn")


def first_urgent_risk(summary: dict[str, Any]) -> dict[str, Any] | None:
    return next((item for item in dict_list(summary.get("items")) if item.get("triage") in ("urgent", "critical")), None)


def risk_identity(item: dict[str, Any] | None) -> Any:
    return {"pawnId": item.get("pawnId"), "condition": item.get("condition"), "triage": item.get("triage")} if item else None


def risk_key(item: dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("pawnId") or ""), str(item.get("condition") or ""))


def available_resources(state: dict[str, Any]) -> dict[str, Any]:
    resources = state.get("resources") if isinstance(state.get("resources"), dict) else {}
    return resources.get("available") if isinstance(resources.get("available"), dict) else resources


def labor(state: dict[str, Any]) -> dict[str, Any]:
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    return operations.get("labor") if isinstance(operations.get("labor"), dict) else {}


def food_nutrition(resources: dict[str, Any]) -> float:
    food = resources.get("food") if isinstance(resources.get("food"), dict) else {}
    return number(food.get("totalNutrition"))


def number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def int_value(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def int_value_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
