"""Safe model-result reduction for continuation-context headroom."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable


DEFAULT_FINALIZATION_HEADROOM_TOKENS = 2_000
DEFAULT_ACTION_BURST_TARGET_TOKENS = 34_000
DEFAULT_ACTION_BURST_HARD_LIMIT_TOKENS = 36_000
SMALL_ACTION_CRITICAL_RESULT_CHARS = 1_600

_CATALOG_TOOLS = {
    "list_build_options",
    "get_build_info",
    "list_growable_plants",
    "list_recipes",
}
_REDUCIBLE_READ_TOOLS = _CATALOG_TOOLS | {"get_colony_state", "inspect_map"}
_VALIDATION_TOOLS = {"check_build_placements", "check_zone_placement"}


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
    *,
    preserve_action_critical: bool = False,
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

    candidates: list[tuple[int, int, int, str, str, str]] = []
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
        before_chars = len(str(output.get("output") or ""))
        if is_action_critical_read(tool, arguments) and (
            preserve_action_critical or before_chars <= SMALL_ACTION_CRITICAL_RESULT_CHARS
        ):
            continue
        replacement = _bounded_result(tool, payload, arguments)
        encoded = json.dumps(replacement, separators=(",", ":"), ensure_ascii=True)
        if len(encoded) >= before_chars:
            continue
        priority = _reduction_priority(tool, arguments)
        savings = before_chars - len(encoded)
        candidates.append((priority, -savings, index, tool, call_id, encoded))

    candidates.sort()
    reductions: list[HeadroomReduction] = []
    for _, _, index, tool, call_id, encoded in candidates:
        before_chars = len(str(fitted[index].get("output") or ""))
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
        str(getattr(call, "call_id", "")): (
            str(getattr(call, "name", "")),
            _arguments(getattr(call, "arguments", "{}")),
        )
        for call in tool_calls
    }
    for output in minimized:
        if output.get("type") != "function_call_output":
            continue
        tool, arguments = calls.get(str(output.get("call_id") or ""), ("", {}))
        payload = _payload(output)
        if tool == "inspect_map" and isinstance(payload, dict) and (
            payload.get("success") is True or payload.get("reason") == "contextHeadroomExceeded"
        ):
            bounds = _map_bounds(payload, arguments)
            marker = {
                "success": False,
                "reason": "contextHeadroomExceeded",
                "requestedBounds": bounds,
                "requestedCellCount": _cell_count(bounds),
                "geometryIncluded": False,
                "partialGeometry": False,
                "requerySmallerRegion": True,
                "controllerLimited": True,
                "authoritativeGameplayBlocker": False,
                "truncated": True,
            }
            _replace_if_smaller(output, marker)
            continue
        if tool not in _REDUCIBLE_READ_TOOLS:
            continue
        if payload is None or payload.get("success") is not True:
            continue
        marker = {
            "success": False,
            "reason": "controllerContextLimit",
            "tool": tool,
            "controllerLimited": True,
            "authoritativeGameplayBlocker": False,
            "resultWithheldForFinalization": True,
            "truncated": True,
        }
        for field in ("section", "snapshotVersion", "count", "totalCount", "category", "search"):
            if payload.get(field) is not None:
                marker[field] = copy.deepcopy(payload[field])
            elif arguments.get(field) is not None:
                marker[field] = copy.deepcopy(arguments[field])
        _replace_if_smaller(output, marker)
    return minimized


def has_exact_action_critical_result(outputs: list[dict[str, Any]], tool_calls: list[Any]) -> bool:
    calls = {
        str(getattr(call, "call_id", "")): (
            str(getattr(call, "name", "")),
            _arguments(getattr(call, "arguments", "{}")),
        )
        for call in tool_calls
    }
    for output in outputs:
        tool, arguments = calls.get(str(output.get("call_id") or ""), ("", {}))
        payload = _payload(output)
        if payload is None or payload.get("success") is not True:
            continue
        if payload.get("resultReduced") is True or payload.get("truncated") is True:
            continue
        if payload.get("controllerLimited") is True:
            continue
        if tool in _VALIDATION_TOOLS and payload.get("valid") is True:
            return True
        if tool == "inspect_map" and _complete_action_map(payload, arguments):
            return True
        if is_action_critical_read(tool, arguments) and _stable_references(payload, 1):
            return True
    return False


def is_action_critical_read(tool: str, arguments: dict[str, Any]) -> bool:
    if tool == "inspect_map":
        cell_count = _cell_count(_map_bounds({}, arguments))
        return cell_count is not None and 0 < cell_count <= 100
    if tool == "get_build_info":
        return True
    if tool == "list_build_options":
        return bool(str(arguments.get("search") or "").strip())
    if tool == "list_recipes":
        return bool(str(arguments.get("worktable_id") or "").strip())
    if tool == "get_colony_state":
        return str(arguments.get("section") or "").lower() in {"research", "equipment", "apparel"}
    return False


def _complete_action_map(payload: dict[str, Any], arguments: dict[str, Any]) -> bool:
    bounds = _map_bounds(payload, arguments)
    cell_count = _cell_count(bounds)
    return (
        cell_count is not None
        and 0 < cell_count <= 100
        and isinstance(payload.get("terrainPalette"), list)
        and isinstance(payload.get("terrainRows"), list)
        and payload.get("geometryIncluded") is not False
    )


def _reduction_priority(tool: str, arguments: dict[str, Any]) -> int:
    if tool == "inspect_map":
        return 0
    if tool in _CATALOG_TOOLS and not is_action_critical_read(tool, arguments):
        return 1
    if tool == "get_colony_state" and not is_action_critical_read(tool, arguments):
        return 2
    return 3


def _replace_if_smaller(output: dict[str, Any], replacement: dict[str, Any]) -> bool:
    before = str(output.get("output") or "")
    encoded = json.dumps(replacement, separators=(",", ":"), ensure_ascii=True)
    if len(encoded) >= len(before):
        return False
    output["output"] = encoded
    return True


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
            "controllerLimited": True,
            "authoritativeGameplayBlocker": False,
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
