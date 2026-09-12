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
DEFAULT_MAX_SESSION_MODEL_REQUESTS = 80
DEFAULT_VERIFICATION_GRACE_TICKS = 1_500
DEFAULT_URGENT_VERIFICATION_GRACE_TICKS = 250

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
    max_session_model_requests: int = DEFAULT_MAX_SESSION_MODEL_REQUESTS
    verification_grace_ticks: int = DEFAULT_VERIFICATION_GRACE_TICKS
    urgent_verification_grace_ticks: int = DEFAULT_URGENT_VERIFICATION_GRACE_TICKS
    allow_unpriced_session: bool = False

    def __post_init__(self) -> None:
        for name in (
            "normal_cooldown_ticks", "max_decisions", "max_immediate_followups",
            "max_problem_attempts", "max_consecutive_failures", "max_consecutive_uncertain",
            "max_session_model_requests", "verification_grace_ticks",
            "urgent_verification_grace_ticks",
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


@dataclass(frozen=True, slots=True)
class RecoveryVerification:
    trigger: DecisionTrigger
    fingerprint: str
    attempt: int
    baseline: dict[str, Any]
    started_tick: int | None
    grace_ticks: int


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
        pricing = getattr(controller, "pricing", None)
        pricing_available = bool(getattr(pricing, "base_configured", False))
        if (
            self.config.max_session_spend is not None
            and not pricing_available
            and not self.config.allow_unpriced_session
        ):
            raise ValueError(
                "Autonomous spend cap requires calculable pricing. Configure input/output pricing "
                "or explicitly allow an unpriced session."
            )
        self.log = logger or print
        self.halted = False
        self.halt_reason: str | None = None
        self.pause_result: dict[str, Any] | None = None
        self.decision_count = 0
        self.model_requests = 0
        self.session_cost = 0.0
        self.session_cost_calculable = pricing_available
        self.consecutive_failures = 0
        self.consecutive_uncertain = 0
        self.consecutive_immediate_followups = 0
        self._last_decision_tick: int | None = None
        self._last_handled_fingerprint: str | None = None
        self._problem_attempts: dict[str, int] = {}
        self._pending_trigger: DecisionTrigger | None = None
        self._pending_recovery: DecisionTrigger | None = None
        self._verification: RecoveryVerification | None = None
        self._observation_scope: tuple[str | None, str | None] | None = None

    def step(self) -> SchedulerStep:
        if self.halted:
            return SchedulerStep("halted", reason=self.halt_reason)

        observed = self.observer.observe_once()
        before = self.observer.current or {}
        self._accept_observation_scope(before)
        if (
            observed.should_trigger
            and observed.priority == "urgent"
            and observed.fingerprint != self._last_handled_fingerprint
        ):
            selected, immediate = observed, False
        else:
            verification_step = self._advance_verification(observed, before)
            if verification_step is not None:
                return verification_step
            selected, immediate = self._select_trigger(observed)
        if selected is None:
            return SchedulerStep("observed", trigger=observed)

        if self.decision_count >= self.config.max_decisions:
            self._halt("session_decision_limit", selected)
            return SchedulerStep("halted", trigger=selected, reason=self.halt_reason)
        if self.model_requests >= self.config.max_session_model_requests:
            self._halt("session_model_request_limit", selected)
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
        outcome = self._run_decision(payload)
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
        result = self._process_outcome(selected, outcome, fingerprint, attempt, progress, before, after)
        if post_trigger is not None and post_trigger.should_trigger and not self.halted:
            if post_trigger.fingerprint != selected.fingerprint and self._pending_trigger is None:
                self._pending_trigger = post_trigger
        return result

    def _advance_verification(
        self, observed: DecisionTrigger, current: dict[str, Any]
    ) -> SchedulerStep | None:
        verification = self._verification
        if verification is None:
            return None
        if self._pending_trigger is not None and self._pending_trigger.priority == "urgent":
            return None
        if meaningful_progress(verification.trigger, verification.baseline, current):
            self._verification = None
            self._problem_attempts.pop(verification.fingerprint, None)
            self.consecutive_immediate_followups = 0
            self._retain_observed_trigger(observed, verification.trigger)
            return SchedulerStep(
                "verification_resolved", verification.trigger,
                meaningful_progress=True, reason="authoritative_progress_during_grace",
            )
        tick = state_ticks(current)
        if (
            tick is None
            or verification.started_tick is None
            or tick < verification.started_tick
            or tick - verification.started_tick < verification.grace_ticks
        ):
            self._retain_observed_trigger(observed, verification.trigger)
            return SchedulerStep(
                "verification_wait", verification.trigger,
                meaningful_progress=False, reason="verification_grace_active",
            )

        self._verification = None
        if verification.attempt >= self.config.max_problem_attempts:
            self._halt("repeated_no_progress", verification.trigger, verification.attempt)
            return SchedulerStep(
                "halted", verification.trigger, meaningful_progress=False, reason=self.halt_reason
            )
        self._pending_recovery = verification.trigger
        return None

    def _retain_observed_trigger(
        self, observed: DecisionTrigger, verified_trigger: DecisionTrigger
    ) -> None:
        if (
            observed.should_trigger
            and observed.fingerprint != verified_trigger.fingerprint
            and self._pending_trigger is None
        ):
            self._pending_trigger = observed

    def _run_decision(self, payload: dict[str, Any]) -> DecisionOutcome:
        remaining = self.config.max_session_model_requests - self.model_requests
        configured = getattr(self.controller, "max_model_requests_per_cycle", None)
        if isinstance(configured, int) and not isinstance(configured, bool):
            self.controller.max_model_requests_per_cycle = min(configured, remaining)
            try:
                return self.controller.run_once(payload)
            finally:
                self.controller.max_model_requests_per_cycle = configured
        return self.controller.run_once(payload)

    def _select_trigger(self, observed: DecisionTrigger) -> tuple[DecisionTrigger | None, bool]:
        if observed.should_trigger and observed.priority == "urgent":
            if observed.fingerprint != self._last_handled_fingerprint:
                return observed, False
        if self._pending_trigger is not None and self._pending_trigger.priority == "urgent":
            trigger = self._pending_trigger
            self._pending_trigger = None
            return trigger, False
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
        before: dict[str, Any],
        after: dict[str, Any],
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
        if (
            self.config.max_session_spend is not None
            and outcome.cycle_cost is None
            and not self.config.allow_unpriced_session
        ):
            self._halt("session_cost_unavailable", trigger, attempt)
            return SchedulerStep("halted", trigger, outcome, progress, self.halt_reason)
        if self.model_requests >= self.config.max_session_model_requests:
            self._halt("session_model_request_limit", trigger, attempt)
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
        grace = (
            self.config.urgent_verification_grace_ticks
            if trigger.priority == "urgent"
            else self.config.verification_grace_ticks
        )
        self._verification = RecoveryVerification(
            trigger=trigger,
            fingerprint=fingerprint,
            attempt=attempt,
            baseline=copy.deepcopy(before),
            started_tick=state_ticks(after) or outcome.final_ticks_game or trigger.ticks_game,
            grace_ticks=grace,
        )
        return SchedulerStep("verification_pending", trigger, outcome, progress, "awaiting_authoritative_progress")

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
            self._verification = None
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
        return productive_idleness_improved(trigger, before, after)
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


def productive_idleness_improved(
    trigger: DecisionTrigger, before: dict[str, Any], after: dict[str, Any]
) -> bool:
    if capable_idle_count(after) < capable_idle_count(before):
        return True
    evidence_ids = {
        str(value) for value in (trigger.evidence or {}).get("capableIdlePawnIds", []) if value
    }
    if not evidence_ids:
        evidence_ids = capable_idle_ids(before)
    current_ids = capable_idle_ids(after)
    return bool(current_ids and evidence_ids - current_ids)


def capable_idle_ids(state: dict[str, Any]) -> set[str]:
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    labor = operations.get("labor") if isinstance(operations.get("labor"), dict) else {}
    return {
        str(item.get("id"))
        for item in list_items(labor.get("capableIdleColonists"))
        if item.get("id")
    }


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
