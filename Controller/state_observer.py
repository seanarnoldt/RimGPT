"""Read-only ownership of passive authoritative-state observations."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

from bridge import RimWorldBridge
from decision_trigger import DecisionTrigger, TriggerContext, TriggerEvaluator
from state_store import StateStore


class StateObserver:
    """Poll `/state`; keep live transition state and trigger history in memory."""

    def __init__(
        self,
        bridge: RimWorldBridge,
        evaluator: TriggerEvaluator | None = None,
        state_root: str | Path | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.bridge = bridge
        self.evaluator = evaluator or TriggerEvaluator()
        self._previous: dict[str, Any] | None = None
        self._current: dict[str, Any] | None = None
        self._store = StateStore(state_root, logger=logger or (lambda _message: None))

    @property
    def previous(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._previous)

    @property
    def current(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._current)

    def acknowledge_review(self) -> None:
        """Reset periodic cadence after a future scheduler handles a review."""
        self.evaluator.acknowledge_review()

    def acknowledge_trigger(self, trigger: DecisionTrigger) -> None:
        self.evaluator.acknowledge_trigger(trigger)

    def observe_once(self) -> DecisionTrigger:
        snapshot = self.bridge.get_state()
        if not isinstance(snapshot, dict):
            raise ValueError("Bridge /state response must be a JSON object")

        previous = self._current
        self._previous = copy.deepcopy(previous)
        self._current = copy.deepcopy(snapshot)

        # This activates only read paths. Passive observations never persist or
        # reconcile any decision artifact.
        self._store.update_current_state(snapshot, persist=False)
        context = TriggerContext(
            handoff=self._store.get_decision_handoff(),
            stall_metadata=self._store.get_stall_metadata(),
            risk_metadata=self._store.get_risk_metadata(),
        )
        return self.evaluator.evaluate(previous, snapshot, context)
