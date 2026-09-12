"""Bounded autonomous trigger scheduling around one-cycle AgentController calls."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

from decision_outcome import DecisionOutcome
from decision_trigger import DecisionTrigger
from progress_tracking import build_progress_signals


DEFAULT_NORMAL_COOLDOWN_TICKS = 2_500
DEFAULT_MAX_AUTONOMOUS_DECISIONS = 20
DEFAULT_MAX_IMMEDIATE_FOLLOWUPS = 2
DEFAULT_MAX_PROBLEM_ATTEMPTS = 3
DEFAULT_MAX_CONSECUTIVE_FAILURES = 2
DEFAULT_MAX_CONSECUTIVE_UNCERTAIN = 2
DEFAULT_MAX_SESSION_SPEND = 5.0

RECOVERABLE_KINDS = {
    "productive_idleness",
    "idle_actionable_work",
    "project_task_stalled",
    "construction_blocked",
    "resource_deterioration",
    "risk_escalation",
    "new_threat",
    "threat_increase",
    "colonist_downed",
}


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    normal_cooldown_ticks: int = DEFAULT_NORMAL_COOLDOWN_TICKS
    max_decisions: int = DEFAULT_MAX_AUTONOMOUS_DECISIONS
    max_immediate_followups: int = DEFAULT_MAX_IMMEDIATE_FOLLOWUPS
    max_problem_attempts: int = DEFAULT_MAX_PROBLEM_ATTEMPTS
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES
    max_consecutive_uncertain: int = DEFAULT_MAX_CONSECUTIVE_UNCERTAIN
    max_session_spend: float | None = DEFAULT_MAX_SESSION_SPEND

    def __post_init__(self) -> None:
        for name in (
            "normal_cooldown_ticks", "max_decisions", "max_immediate_followups",
            "max_problem_attempts", "max_consecutive_failures", "max_consecutive_uncertain",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.max_session_spend is not None and self.max_session_spend <= 0:
            raise ValueError("max_session_spend must be positive when configured")


@dataclass(frozen=True, slots=True)
class SchedulerStep:
    status: str
    trigger: DecisionTrigger | None = None
    outcome: DecisionOutcome | None = None
    meaningful_progress: bool | None = None
    reason: str | None = None


class AutonomousScheduler:
    """Coordinates observations and bounded strategic decisions in memory."""

    def __init__(
        self,
        observer: Any,
        controller: Any,
        bridge: Any,
        *,
        config: SchedulerConfig | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.observer = observer
        self.controller = controller
        self.bridge = bridge
        self.config = config or SchedulerConfig()
        self.log = logger or print
        self.halted = False
        self.halt_reason: str | None = None
        self.pause_result: dict[str, Any] | None = None
        self.decision_count = 0
        self.model_requests = 0
        self.session_cost = 0.0
        self.session_cost_calculable = True
        self.consecutive_failures = 0
        self.consecutive_uncertain = 0
        self.consecutive_immediate_followups = 0
        self._last_decision_tick: int | None = None
        self._last_handled_fingerprint: str | None = None
        self._problem_attempts: dict[str, int] = {}
        self._pending_trigger: DecisionTrigger | None = None
        self._pending_recovery: DecisionTrigger | None = None
        self._observation_scope: tuple[str | None, str | None] | None = None

    def step(self) -> SchedulerStep:
        if self.halted:
            return SchedulerStep("halted", reason=self.halt_reason)

        observed = self.observer.observe_once()
        before = self.observer.current or {}
        self._accept_observation_scope(before)
        selected, immediate = self._select_trigger(observed)
        if selected is None:
            return SchedulerStep("observed", trigger=observed)

        if self.decision_count >= self.config.max_decisions:
            self._halt("session_decision_limit", selected)
            return SchedulerStep("halted", trigger=selected, reason=self.halt_reason)

        if immediate:
            if self.consecutive_immediate_followups >= self.config.max_immediate_followups:
                self._halt("immediate_followup_limit", selected)
                return SchedulerStep("halted", trigger=selected, reason=self.halt_reason)
            self.consecutive_immediate_followups += 1
        else:
            self.consecutive_immediate_followups = 0
            if selected.priority != "urgent" and self._cooldown_active(selected, before):
                self._pending_trigger = selected
                return SchedulerStep("cooldown", trigger=selected, reason="normal_decision_cooldown")

        fingerprint = scoped_problem_fingerprint(selected, before)
        attempt = self._problem_attempts.get(fingerprint, 0) + 1
        payload = trigger_payload(selected, fingerprint, attempt if immediate else 1, immediate)
        self.log(
            f"[AUTONOMY] decision={self.decision_count + 1} trigger={selected.kind} "
            f"priority={selected.priority} attempt={attempt}"
        )
        outcome = self.controller.run_once(payload)
        self.decision_count += 1
        self.model_requests += max(0, outcome.model_requests)
        self._last_decision_tick = outcome.final_ticks_game or selected.ticks_game
        self._last_handled_fingerprint = selected.fingerprint
        self._record_cost(outcome)

        try:
            post_trigger = self.observer.observe_once()
            after = self.observer.current or {}
        except Exception as exc:
            post_trigger = None
            after = before
            self.log(f"[WARNING] Post-decision observation failed: {exc}")

        progress = meaningful_progress(selected, before, after)
        result = self._process_outcome(selected, outcome, fingerprint, attempt, progress)
        if post_trigger is not None and post_trigger.should_trigger and not self.halted:
            if post_trigger.fingerprint != selected.fingerprint and self._pending_trigger is None:
                self._pending_trigger = post_trigger
        return result

    def _select_trigger(self, observed: DecisionTrigger) -> tuple[DecisionTrigger | None, bool]:
        if observed.should_trigger and observed.priority == "urgent":
            if observed.fingerprint != self._last_handled_fingerprint:
                return observed, False
        if self._pending_recovery is not None:
            trigger = self._pending_recovery
            self._pending_recovery = None
            return trigger, True
        if self._pending_trigger is not None:
            trigger = self._pending_trigger
            self._pending_trigger = None
            return trigger, False
        if observed.should_trigger and observed.fingerprint != self._last_handled_fingerprint:
            return observed, False
        return None, False

    def _cooldown_active(self, trigger: DecisionTrigger, current: dict[str, Any]) -> bool:
        observed_tick = state_ticks(current)
        return (
            self._last_decision_tick is not None
            and observed_tick is not None
            and observed_tick >= self._last_decision_tick
            and observed_tick - self._last_decision_tick < self.config.normal_cooldown_ticks
        )

    def _process_outcome(
        self,
        trigger: DecisionTrigger,
        outcome: DecisionOutcome,
        fingerprint: str,
        attempt: int,
        progress: bool,
    ) -> SchedulerStep:
        if outcome.uncertain_commands_remained:
            self.consecutive_uncertain += 1
        else:
            self.consecutive_uncertain = 0
        if self.consecutive_uncertain >= self.config.max_consecutive_uncertain:
            self._halt("repeated_uncertain_commands", trigger, attempt)
            return SchedulerStep("halted", trigger, outcome, progress, self.halt_reason)

        if self._spend_limit_reached():
            self._halt("session_spend_limit", trigger, attempt)
            return SchedulerStep("halted", trigger, outcome, progress, self.halt_reason)
        if self.decision_count >= self.config.max_decisions:
            self._halt("session_decision_limit", trigger, attempt)
            return SchedulerStep("halted", trigger, outcome, progress, self.halt_reason)

        if not outcome.success:
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.config.max_consecutive_failures:
                self._halt("repeated_failed_outcomes", trigger, attempt)
                return SchedulerStep("halted", trigger, outcome, progress, self.halt_reason)
            self._pending_recovery = trigger
            return SchedulerStep("decision_failed", trigger, outcome, progress, outcome.termination_reason)

        self.consecutive_failures = 0
        self._acknowledge(trigger)

        if trigger.kind not in RECOVERABLE_KINDS or progress:
            self._problem_attempts.pop(fingerprint, None)
            self.consecutive_immediate_followups = 0
            return SchedulerStep("decided", trigger, outcome, progress)

        self._problem_attempts[fingerprint] = attempt
        if attempt >= self.config.max_problem_attempts:
            self._halt("repeated_no_progress", trigger, attempt)
            return SchedulerStep("halted", trigger, outcome, progress, self.halt_reason)
        self._pending_recovery = trigger
        return SchedulerStep("recovery_pending", trigger, outcome, progress, "no_meaningful_progress")

    def _record_cost(self, outcome: DecisionOutcome) -> None:
        if outcome.cycle_cost is None:
            self.session_cost_calculable = False
            return
        self.session_cost += max(0.0, outcome.cycle_cost)

    def _accept_observation_scope(self, state: dict[str, Any]) -> None:
        game = state.get("game") if isinstance(state.get("game"), dict) else {}
        scope = (
            game.get("colonyLineageId") if isinstance(game.get("colonyLineageId"), str) else None,
            game.get("currentMapId") if isinstance(game.get("currentMapId"), str) else None,
        )
        if self._observation_scope is not None and scope != self._observation_scope:
            self._problem_attempts.clear()
            self._pending_trigger = None
            self._pending_recovery = None
            self._last_handled_fingerprint = None
            self._last_decision_tick = None
            self.consecutive_immediate_followups = 0
        self._observation_scope = scope

    def _spend_limit_reached(self) -> bool:
        return (
            self.config.max_session_spend is not None
            and self.session_cost_calculable
            and self.session_cost >= self.config.max_session_spend
        )

    def _acknowledge(self, trigger: DecisionTrigger) -> None:
        acknowledge = getattr(self.observer, "acknowledge_trigger", None)
        if callable(acknowledge):
            acknowledge(trigger)
        elif trigger.kind == "periodic_review":
            self.observer.acknowledge_review()

    def _halt(self, reason: str, trigger: DecisionTrigger, attempts: int | None = None) -> None:
        if self.halted:
            return
        self.halted = True
        self.halt_reason = reason
        try:
            self.pause_result = self.bridge.send_command_and_wait({"command": "setSpeed", "speed": 0})
        except Exception as exc:
            self.pause_result = {"status": "unreachable", "success": False, "error": str(exc)}
        paused = self.pause_result.get("success") is True
        cost = f"{self.session_cost:.6f}" if self.session_cost_calculable else "unavailable"
        self.log("[AUTONOMY HALTED]")
        self.log(f"reason={reason}")
        self.log(f"trigger={trigger.kind}")
        self.log(f"attempts={attempts if attempts is not None else self._problem_attempts.get(problem_fingerprint(trigger), 0)}")
        self.log(f"gamePaused={'true' if paused else 'unconfirmed'}")
        self.log(f"modelRequests={self.model_requests}")
        self.log(f"sessionCost={cost}")


def trigger_payload(
    trigger: DecisionTrigger, fingerprint: str, attempt: int, recovery: bool
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "autonomousDecision",
        "kind": trigger.kind,
        "priority": trigger.priority,
        "reason": trigger.reason,
        "fingerprint": fingerprint,
        "recoveryAttempt": attempt if recovery else 0,
        "previousAttemptMadeProgress": False if recovery else None,
    }
    for key, value in (trigger.evidence or {}).items():
        if key in ("capableIdleColonists", "capableIdlePawnIds"):
            payload[key] = copy.deepcopy(value)
    if recovery:
        payload["recoveryGuidance"] = (
            "The prior completed decision did not produce meaningful authoritative progress. "
            "Materially change strategy; do not repeat the same action or explanation unchanged."
        )
    return payload


def problem_fingerprint(trigger: DecisionTrigger) -> str:
    return trigger.fingerprint or f"{trigger.kind}:unidentified"


def scoped_problem_fingerprint(trigger: DecisionTrigger, state: dict[str, Any]) -> str:
    game = state.get("game") if isinstance(state.get("game"), dict) else {}
    lineage = game.get("colonyLineageId") or "unknown-colony"
    map_id = game.get("currentMapId") or "unknown-map"
    return f"{lineage}:{map_id}:{problem_fingerprint(trigger)}"


def meaningful_progress(trigger: DecisionTrigger, before: dict[str, Any], after: dict[str, Any]) -> bool:
    if not before or not after:
        return False
    progress = build_progress_signals(before, after)
    categories = set(progress.get("categories") or [])
    if trigger.kind in ("productive_idleness", "idle_actionable_work"):
        if capable_idle_count(after) < capable_idle_count(before):
            return True
        return (
            bool(categories & {"construction", "research", "growing", "designation", "blocker", "work", "room"})
            or research_started(before, after)
        )
    if trigger.kind in ("new_threat", "threat_increase", "colonist_downed", "risk_escalation"):
        return emergency_improved(before, after) or bool(categories & {"blocker", "work"})
    if trigger.kind in ("construction_blocked", "project_task_stalled"):
        return bool(categories & {"construction", "research", "growing", "designation", "blocker", "work", "room"}) or resource_increased(before, after)
    if trigger.kind == "resource_deterioration":
        return resource_increased(before, after)
    return bool(progress.get("relevantStateChanged"))


def capable_idle_count(state: dict[str, Any]) -> int:
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    labor = operations.get("labor") if isinstance(operations.get("labor"), dict) else {}
    value = labor.get("capableIdleColonistCount")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def emergency_improved(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if len(list_items(after.get("threats"))) < len(list_items(before.get("threats"))):
        return True
    old_downed = sum(1 for pawn in list_items(before.get("colonists")) if health_flag(pawn, "downed"))
    new_downed = sum(1 for pawn in list_items(after.get("colonists")) if health_flag(pawn, "downed"))
    return new_downed < old_downed


def resource_increased(before: dict[str, Any], after: dict[str, Any]) -> bool:
    old = available_resources(before)
    new = available_resources(after)
    for key in ("wood", "steel", "components", "medicine"):
        if numeric(new.get(key)) > numeric(old.get(key)):
            return True
    old_food = old.get("food") if isinstance(old.get("food"), dict) else {}
    new_food = new.get("food") if isinstance(new.get("food"), dict) else {}
    return numeric(new_food.get("totalNutrition")) > numeric(old_food.get("totalNutrition"))


def research_started(before: dict[str, Any], after: dict[str, Any]) -> bool:
    old = before.get("research") if isinstance(before.get("research"), dict) else {}
    new = after.get("research") if isinstance(after.get("research"), dict) else {}
    old_current = old.get("current") if isinstance(old.get("current"), dict) else {}
    new_current = new.get("current") if isinstance(new.get("current"), dict) else {}
    return not old_current.get("defName") and bool(new_current.get("defName"))


def available_resources(state: dict[str, Any]) -> dict[str, Any]:
    resources = state.get("resources") if isinstance(state.get("resources"), dict) else {}
    return resources.get("available") if isinstance(resources.get("available"), dict) else resources


def list_items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def health_flag(pawn: dict[str, Any], key: str) -> bool:
    health = pawn.get("health") if isinstance(pawn.get("health"), dict) else {}
    return health.get(key) is True


def numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def state_ticks(state: dict[str, Any]) -> int | None:
    snapshot = state.get("snapshot") if isinstance(state.get("snapshot"), dict) else {}
    game = state.get("game") if isinstance(state.get("game"), dict) else {}
    value = snapshot.get("ticksGame", game.get("ticksGame"))
    return value if isinstance(value, int) and not isinstance(value, bool) else None
