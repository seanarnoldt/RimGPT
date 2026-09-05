"""Stable prompt identity and installed-SDK feature detection."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable


RIMGPT_PROMPT_VERSION = "context-memory-v1-m8"
DEFAULT_PROMPT_CACHE_MODE = "implicit"
PROMPT_CACHE_TTL = "30m"
DEFAULT_COMPACT_THRESHOLD_TOKENS = 20_000
DEFAULT_MAX_COMPACTIONS_PER_CYCLE = 1


@dataclass(frozen=True)
class ResponsesFeatures:
    prompt_cache_key: bool
    prompt_cache_options: bool
    compact: bool


def detect_responses_features(responses: Any) -> ResponsesFeatures:
    create = getattr(responses, "create", None)
    compact = getattr(responses, "compact", None)
    return ResponsesFeatures(
        prompt_cache_key=callable_accepts_keyword(create, "prompt_cache_key"),
        prompt_cache_options=callable_accepts_keyword(create, "prompt_cache_options"),
        compact=callable(compact)
        and callable_accepts_keyword(compact, "previous_response_id")
        and callable_accepts_keyword(compact, "input"),
    )


def callable_accepts_keyword(function: Callable[..., Any] | None, name: str) -> bool:
    if not callable(function):
        return False
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):
        return False
    return name in parameters or any(item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values())


def build_prompt_cache_key(model: str, prompt_version: str = RIMGPT_PROMPT_VERSION) -> str:
    safe_version = safe_cache_component(prompt_version)
    safe_model = safe_cache_component(model)
    return f"rimgpt:{safe_version}:{safe_model}"


def safe_cache_component(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value).strip()).strip("-").lower()
    return (normalized or "unknown")[:80]


def model_supports_prompt_cache_options(model: str) -> bool:
    normalized = model.lower()
    return normalized.startswith("gpt-5.6") or normalized.startswith("gpt-6")


def prompt_cache_request_fields(
    features: ResponsesFeatures,
    model: str,
    mode: str = DEFAULT_PROMPT_CACHE_MODE,
) -> dict[str, Any]:
    if mode not in ("disabled", "implicit"):
        raise ValueError("Prompt cache mode must be disabled or implicit")
    if mode == "disabled" or not features.prompt_cache_key:
        return {}
    fields: dict[str, Any] = {"prompt_cache_key": build_prompt_cache_key(model)}
    if features.prompt_cache_options and model_supports_prompt_cache_options(model):
        fields["prompt_cache_options"] = {"mode": mode, "ttl": PROMPT_CACHE_TTL}
    return fields


def compacted_output_as_input(compacted: Any) -> list[dict[str, Any]]:
    output = getattr(compacted, "output", None)
    if not isinstance(output, list) or not output:
        raise ValueError("Compaction response did not contain output items")
    items: list[dict[str, Any]] = []
    for item in output:
        if isinstance(item, dict):
            value = dict(item)
        elif hasattr(item, "model_dump"):
            value = item.model_dump(exclude_none=True)
        else:
            raise TypeError("Compaction output item is not serializable as Responses input")
        if not value.get("type"):
            raise ValueError("Compaction output item has no type")
        items.append(value)
    return items
