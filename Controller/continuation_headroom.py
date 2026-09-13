"""Safe model-result reduction for continuation-context headroom."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable


DEFAULT_FINALIZATION_HEADROOM_TOKENS = 2_000

_CATALOG_TOOLS = {
    "list_build_options",
    "get_build_info",
    "list_growable_plants",
    "list_recipes",
}
_REDUCIBLE_READ_TOOLS = _CATALOG_TOOLS | {"get_colony_state", "inspect_map"}


@dataclass(frozen=True)
class HeadroomReduction:
    tool: str
    call_id: str
    before_chars: int
    after_chars: int


def fit_read_results(
    outputs: list[dict[str, Any]],
    tool_calls: list[Any],
    estimate_tokens: Callable[[list[dict[str, Any]]], int],
    target_tokens: int,
) -> tuple[list[dict[str, Any]], list[HeadroomReduction], int]:
    """Reduce successful broad reads until the prospective request fits."""
    fitted = copy.deepcopy(outputs)
    calls = {
        str(getattr(call, "call_id", "")): (
            str(getattr(call, "name", "")),
            _arguments(getattr(call, "arguments", "{}")),
        )
        for call in tool_calls
    }
    estimated = estimate_tokens(fitted)
    if estimated <= target_tokens:
        return fitted, [], estimated

    candidates: list[tuple[int, int, str, str, dict[str, Any]]] = []
    for index, output in enumerate(fitted):
        if output.get("type") != "function_call_output":
            continue
        call_id = str(output.get("call_id") or "")
        tool, arguments = calls.get(call_id, ("", {}))
        if tool not in _REDUCIBLE_READ_TOOLS:
            continue
        payload = _payload(output)
        if payload is None or payload.get("success") is not True:
            continue
        candidates.append((len(str(output.get("output") or "")), index, tool, call_id, arguments))

    candidates.sort(key=lambda item: (item[0], item[2] == "inspect_map"), reverse=True)
    reductions: list[HeadroomReduction] = []
    for before_chars, index, tool, call_id, arguments in candidates:
        replacement = _bounded_result(tool, _payload(fitted[index]) or {}, arguments)
        encoded = json.dumps(replacement, separators=(",", ":"), ensure_ascii=True)
        fitted[index]["output"] = encoded
        reductions.append(HeadroomReduction(tool, call_id, before_chars, len(encoded)))
        estimated = estimate_tokens(fitted)
        if estimated <= target_tokens:
            break
    return fitted, reductions, estimated


def minimize_for_finalization(
    outputs: list[dict[str, Any]],
    tool_calls: list[Any],
) -> list[dict[str, Any]]:
    """Drop action references after the tool surface has become terminal-only."""
    minimized = copy.deepcopy(outputs)
    calls = {
        str(getattr(call, "call_id", "")): str(getattr(call, "name", ""))
        for call in tool_calls
    }
    for output in minimized:
        if output.get("type") != "function_call_output":
            continue
        tool = calls.get(str(output.get("call_id") or ""), "")
        payload = _payload(output)
        if tool == "inspect_map" and isinstance(payload, dict) and payload.get("reason") == "contextHeadroomExceeded":
            marker = {
                "success": False,
                "reason": "contextHeadroomExceeded",
                "requestedBounds": copy.deepcopy(payload.get("requestedBounds")),
                "requestedCellCount": payload.get("requestedCellCount"),
                "geometryIncluded": False,
                "partialGeometry": False,
                "requerySmallerRegion": True,
                "truncated": True,
            }
            output["output"] = json.dumps(marker, separators=(",", ":"), ensure_ascii=True)
            continue
        if tool not in (_CATALOG_TOOLS | {"get_colony_state"}):
            continue
        if payload is None or payload.get("success") is not True or payload.get("resultReduced") is not True:
            continue
        marker = {
            "success": True,
            "resultReduced": True,
            "reason": "contextHeadroomReservedForFinalization",
            "tool": tool,
            "truncated": True,
        }
        for field in ("section", "snapshotVersion", "count", "totalCount", "category", "search"):
            if payload.get(field) is not None:
                marker[field] = copy.deepcopy(payload[field])
        output["output"] = json.dumps(marker, separators=(",", ":"), ensure_ascii=True)
    return minimized


def _bounded_result(tool: str, payload: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    if tool == "inspect_map":
        bounds = _map_bounds(payload, arguments)
        return {
            "success": False,
            "reason": "contextHeadroomExceeded",
            "message": "Exact requested map geometry does not fit the remaining decision context; inspect a smaller region or finalize.",
            "requestedBounds": bounds,
            "requestedCellCount": _cell_count(bounds),
            "geometryIncluded": False,
            "partialGeometry": False,
            "requerySmallerRegion": True,
            "truncated": True,
        }

    references = _stable_references(payload, 16)
    result: dict[str, Any] = {
        "success": True,
        "resultReduced": True,
        "reason": "contextHeadroomReserved",
        "tool": tool,
        "message": "Noncritical detail was reduced to preserve room to act or finish this decision.",
        "truncated": True,
    }
    for field in ("section", "snapshotVersion", "count", "totalCount", "category", "search"):
        if payload.get(field) is not None:
            result[field] = copy.deepcopy(payload[field])
        elif arguments.get(field) is not None:
            result[field] = copy.deepcopy(arguments[field])
    if references:
        result["references"] = references
        result["referencesIncomplete"] = True
    return result


def _stable_references(value: Any, limit: int) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(item: Any) -> None:
        if len(references) >= limit:
            return
        if isinstance(item, dict):
            selected = {
                field: copy.deepcopy(item[field])
                for field in (
                    "id", "defName", "label", "position", "requiredTerrainAffordance",
                    "stage", "status", "available", "operational",
                )
                if item.get(field) is not None
            }
            if "id" in selected or "defName" in selected:
                key = json.dumps(selected, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                if key not in seen:
                    seen.add(key)
                    references.append(selected)
            for child in item.values():
                visit(child)
                if len(references) >= limit:
                    return
        elif isinstance(item, list):
            for child in item:
                visit(child)
                if len(references) >= limit:
                    return

    visit(value)
    return references


def _map_bounds(payload: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any] | None:
    bounds = payload.get("bounds")
    if isinstance(bounds, dict):
        return copy.deepcopy(bounds)
    names = (("min_x", "minX"), ("min_z", "minZ"), ("max_x", "maxX"), ("max_z", "maxZ"))
    if all(source in arguments for source, _ in names):
        return {target: arguments[source] for source, target in names}
    return None


def _cell_count(bounds: dict[str, Any] | None) -> int | None:
    if not isinstance(bounds, dict):
        return None
    values = [bounds.get(name) for name in ("minX", "minZ", "maxX", "maxZ")]
    if not all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return None
    min_x, min_z, max_x, max_z = values
    if max_x < min_x or max_z < min_z:
        return None
    return (max_x - min_x + 1) * (max_z - min_z + 1)


def _payload(output: dict[str, Any]) -> dict[str, Any] | None:
    value = output.get("output")
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
