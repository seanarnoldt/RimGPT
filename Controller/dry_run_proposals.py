"""Bounded, cycle-local memory of non-authoritative dry-run proposals."""

from __future__ import annotations

import json
from typing import Any


DEFAULT_MAX_DRY_RUN_PROPOSALS = 20


class DryRunProposalLedger:
    def __init__(self, maximum: int = DEFAULT_MAX_DRY_RUN_PROPOSALS) -> None:
        self.maximum = max(1, maximum)
        self._entries: list[dict[str, str]] = []
        self._signatures: set[str] = set()

    def add(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        signature = proposal_signature(tool, arguments)
        duplicate = signature in self._signatures
        summary = proposal_summary(tool, arguments)
        if not duplicate:
            self._entries.append({"signature": signature, "summary": summary})
            self._signatures.add(signature)
            while len(self._entries) > self.maximum:
                removed = self._entries.pop(0)
                self._signatures.discard(removed["signature"])
        return {"duplicate": duplicate, "summary": summary}

    def summaries(self) -> list[str]:
        return [entry["summary"] for entry in self._entries]

    def __len__(self) -> int:
        return len(self._entries)


def proposal_signature(tool: str, arguments: dict[str, Any]) -> str:
    return json.dumps(
        {"tool": tool, "arguments": normalize_proposal_value(arguments)},
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_proposal_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalize_proposal_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        normalized = [normalize_proposal_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    return value


def proposal_summary(tool: str, arguments: dict[str, Any]) -> str:
    if tool == "set_speed":
        return f"Set speed to {arguments.get('speed')}"
    if tool == "allow_all":
        return "Allow all forbidden items"
    if tool in ("create_growing_zone", "create_stockpile"):
        kind = "growing zone" if tool == "create_growing_zone" else "stockpile"
        bounds = _bounds(arguments)
        plant = f" for {arguments.get('plant_def')}" if arguments.get("plant_def") else ""
        return f"Create {kind} at {bounds}{plant}"
    if tool == "place_blueprints":
        placements = arguments.get("placements")
        count = len(placements) if isinstance(placements, list) else 0
        return f"Place {count} blueprints"
    compact = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return f"{tool} {compact}"[:180]


def _bounds(arguments: dict[str, Any]) -> str:
    return (
        f"x={arguments.get('min_x')}..{arguments.get('max_x')},"
        f"z={arguments.get('min_z')}..{arguments.get('max_z')}"
    )
