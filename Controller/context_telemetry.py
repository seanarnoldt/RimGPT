"""Local request-size and API-usage telemetry for the RimGPT controller.

The token estimate is intentionally conservative. It is a preflight safety
guard, not a reproduction of provider billing or model tokenization.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


DEFAULT_MAX_INPUT_TOKENS_PER_REQUEST = 30_000
DEFAULT_MAX_MODEL_REQUESTS_PER_CYCLE = 8
# JSON-heavy prompt material often tokenizes at fewer than four characters per
# token. Three keeps the development guard deliberately conservative.
CONSERVATIVE_CHARS_PER_TOKEN = 3

V3_TOOL_NAMES = {
    "list_recipes", "equip_weapon", "drop_primary_weapon", "wear_apparel",
    "remove_apparel", "assign_bed", "unassign_bed", "add_bill",
    "set_bill_suspended", "remove_bill", "set_bill_target_count",
    "set_power_switch", "set_target_fuel_level", "create_allowed_area",
    "set_allowed_area_cells", "assign_allowed_area", "prioritize_haul",
    "prioritize_rescue", "prioritize_tend", "prioritize_clean",
    "prioritize_refuel", "prioritize_construct",
}


class ModelContextLimitError(RuntimeError):
    def __init__(self, breakdown: "ContextBreakdown", limit: int) -> None:
        self.breakdown = breakdown
        self.limit = limit
        super().__init__(
            "Model context exceeds configured input-token limit. "
            f"estimatedInputTokens={breakdown.estimated_input_tokens} limit={limit}"
        )


class ModelRequestLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class ContextBreakdown:
    system_chars: int
    dynamic_input_chars: int
    tool_schema_chars: int
    v2_tool_schema_chars: int
    v3_tool_schema_chars: int
    full_state_chars: int
    operations_chars: int
    colonists_chars: int
    map_overview_chars: int
    memory_chars: int
    summary_chars: int
    delta_chars: int
    trigger_chars: int
    bootstrap: bool
    bootstrap_chars: int
    full_state_sent: bool
    tool_result_chars_this_round: int
    accumulated_tool_result_chars: int
    carried_context_chars: int
    request_payload_chars: int
    estimated_input_tokens: int

    def as_log_line(self) -> str:
        return (
            "[CONTEXT] "
            f"systemChars={self.system_chars} dynamicInputChars={self.dynamic_input_chars} "
            f"toolSchemaChars={self.tool_schema_chars} "
            f"v2ToolSchemaChars={self.v2_tool_schema_chars} "
            f"v3ToolSchemaChars={self.v3_tool_schema_chars} "
            f"toolResultCharsThisRound={self.tool_result_chars_this_round} "
            f"toolResultCharsAccumulated={self.accumulated_tool_result_chars} "
            f"memoryChars={self.memory_chars} summaryChars={self.summary_chars} "
            f"deltaChars={self.delta_chars} triggerChars={self.trigger_chars} "
            f"bootstrap={str(self.bootstrap).lower()} bootstrapChars={self.bootstrap_chars} "
            f"fullStateChars={self.full_state_chars} operationsChars={self.operations_chars} "
            f"fullStateSent={str(self.full_state_sent).lower()} "
            f"colonistsChars={self.colonists_chars} mapOverviewChars={self.map_overview_chars} "
            f"carriedContextChars={self.carried_context_chars} "
            f"requestEstimateChars={self.request_payload_chars} "
            f"estimatedInputTokens={self.estimated_input_tokens}"
        )


@dataclass(frozen=True)
class Pricing:
    input_per_million: float | None = None
    cached_input_per_million: float | None = None
    output_per_million: float | None = None
    cache_write_per_million: float | None = None

    @classmethod
    def from_environment(cls) -> "Pricing":
        return cls(
            input_per_million=_optional_nonnegative_float("RIMGPT_INPUT_COST_PER_MILLION"),
            cached_input_per_million=_optional_nonnegative_float("RIMGPT_CACHED_INPUT_COST_PER_MILLION"),
            output_per_million=_optional_nonnegative_float("RIMGPT_OUTPUT_COST_PER_MILLION"),
            cache_write_per_million=_optional_nonnegative_float("RIMGPT_CACHE_WRITE_COST_PER_MILLION"),
        )

    def estimate_cost(
        self,
        input_tokens: int | None,
        cached_input_tokens: int | None,
        output_tokens: int | None,
        cache_write_tokens: int | None = None,
    ) -> float | None:
        if input_tokens is None or output_tokens is None:
            return None
        if self.input_per_million is None or self.output_per_million is None:
            return None
        cached = min(max(cached_input_tokens or 0, 0), input_tokens)
        uncached = input_tokens - cached
        cache_write = min(max(cache_write_tokens or 0, 0), uncached)
        regular_uncached = uncached - cache_write
        if cached and self.cached_input_per_million is None:
            return None
        cache_write_rate = (
            self.cache_write_per_million
            if self.cache_write_per_million is not None
            else self.input_per_million
        )
        return (
            (regular_uncached * self.input_per_million)
            + (cache_write * cache_write_rate)
            + (cached * (self.cached_input_per_million or 0.0))
            + (output_tokens * self.output_per_million)
        ) / 1_000_000


@dataclass(frozen=True)
class UsageTelemetry:
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_write_tokens: int | None
    uncached_input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None


def serialized_chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=True, default=str))


def estimate_tokens(chars: int) -> int:
    return (max(chars, 0) + CONSERVATIVE_CHARS_PER_TOKEN - 1) // CONSERVATIVE_CHARS_PER_TOKEN


def measure_context(
    *,
    instructions: str,
    tools: list[dict[str, Any]],
    input_items: list[dict[str, Any]],
    state: dict[str, Any] | None,
    accumulated_tool_result_chars: int,
    carried_context_chars: int,
    context_payload: dict[str, Any] | None = None,
    full_state_sent: bool = False,
    tool_result_chars_this_round: int = 0,
) -> ContextBreakdown:
    system_chars = len(instructions)
    tool_schema_chars = serialized_chars(tools)
    v3_tools = [tool for tool in tools if tool.get("name") in V3_TOOL_NAMES]
    v3_tool_schema_chars = serialized_chars(v3_tools)
    v2_tool_schema_chars = tool_schema_chars - v3_tool_schema_chars
    dynamic_input_chars = serialized_chars(input_items)
    full_state_chars = serialized_chars(state) if state is not None else 0
    operations_chars = serialized_chars(state.get("operations")) if isinstance(state, dict) and "operations" in state else 0
    colonists_chars = serialized_chars(state.get("colonists")) if isinstance(state, dict) and "colonists" in state else 0
    map_overview_chars = serialized_chars(state.get("map")) if isinstance(state, dict) and "map" in state else 0
    context_payload = context_payload if isinstance(context_payload, dict) else {}
    memory_chars = serialized_chars(context_payload.get("strategicMemory")) if "strategicMemory" in context_payload else 0
    summary_chars = serialized_chars(context_payload.get("currentSummary")) if "currentSummary" in context_payload else 0
    delta_value = context_payload.get("changesSinceLastDecision", context_payload.get("changesSinceToolRound"))
    delta_chars = serialized_chars(delta_value) if delta_value is not None else 0
    trigger_chars = serialized_chars(context_payload.get("trigger")) if "trigger" in context_payload else 0
    bootstrap = bool(context_payload.get("bootstrap"))
    bootstrap_chars = serialized_chars(context_payload.get("bootstrapState")) if "bootstrapState" in context_payload else 0
    # Tool-result bytes are already present in either this request's dynamic
    # input or the known continuation history. Keep them separate in the
    # breakdown, but do not count them twice in the total estimate.
    request_payload_chars = system_chars + tool_schema_chars + dynamic_input_chars + carried_context_chars
    return ContextBreakdown(
        system_chars=system_chars,
        dynamic_input_chars=dynamic_input_chars,
        tool_schema_chars=tool_schema_chars,
        v2_tool_schema_chars=v2_tool_schema_chars,
        v3_tool_schema_chars=v3_tool_schema_chars,
        full_state_chars=full_state_chars,
        operations_chars=operations_chars,
        colonists_chars=colonists_chars,
        map_overview_chars=map_overview_chars,
        memory_chars=memory_chars,
        summary_chars=summary_chars,
        delta_chars=delta_chars,
        trigger_chars=trigger_chars,
        bootstrap=bootstrap,
        bootstrap_chars=bootstrap_chars,
        full_state_sent=full_state_sent,
        tool_result_chars_this_round=tool_result_chars_this_round,
        accumulated_tool_result_chars=accumulated_tool_result_chars,
        carried_context_chars=carried_context_chars,
        request_payload_chars=request_payload_chars,
        estimated_input_tokens=estimate_tokens(request_payload_chars),
    )


def extract_usage(response: Any, pricing: Pricing) -> UsageTelemetry:
    usage = _value(response, "usage")
    input_tokens = _as_nonnegative_int(_value(usage, "input_tokens"))
    output_tokens = _as_nonnegative_int(_value(usage, "output_tokens"))
    input_details = _value(usage, "input_tokens_details")
    cached_input_tokens = _as_nonnegative_int(_value(input_details, "cached_tokens"))
    if cached_input_tokens is None:
        cached_input_tokens = _as_nonnegative_int(_value(usage, "cached_input_tokens"))
    cache_write_tokens = _as_nonnegative_int(_value(input_details, "cache_write_tokens"))
    if cache_write_tokens is None:
        cache_write_tokens = _as_nonnegative_int(_value(usage, "cache_write_tokens"))
    uncached_input_tokens = None
    if input_tokens is not None:
        uncached_input_tokens = input_tokens - min(max(cached_input_tokens or 0, 0), input_tokens)
    return UsageTelemetry(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        cache_write_tokens=cache_write_tokens,
        uncached_input_tokens=uncached_input_tokens,
        output_tokens=output_tokens,
        estimated_cost=pricing.estimate_cost(input_tokens, cached_input_tokens, output_tokens, cache_write_tokens),
    )


def _value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_nonnegative_int(value: Any) -> int | None:
    parsed = _as_int(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _optional_nonnegative_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return value
