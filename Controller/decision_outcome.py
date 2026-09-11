"""Structured factual result of one RimGPT decision cycle."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    """Compact cycle result for callers such as a future scheduler.

    The handoff is defensively copied when the outcome is built so it does not
    share ownership with the controller or state store.
    """

    success: bool
    termination_reason: str | None
    final_snapshot_version: int | None
    final_ticks_game: int | None
    colony_lineage_id: str | None
    current_map_id: str | None
    handoff: dict[str, Any] | None
    model_requests: int
    write_commands: int
    uncertain_commands_remained: bool
    cycle_cost: float | None
    dry_run: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "handoff", copy.deepcopy(self.handoff))
