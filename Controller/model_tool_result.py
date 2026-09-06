"""Deterministic model-facing views over raw bridge and controller tool results."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable


DEFAULT_MAX_MODEL_RESULT_CHARS = 12_000
MAX_INSPECT_MAP_CHARS = 24_000
MAX_ERROR_CHARS = 800
CATALOG_TOOLS = {"list_build_options", "get_build_info", "list_growable_plants", "list_recipes"}
CAPABILITY_TOOLS = {"list_capabilities", "enable_capability"}
TERMINAL_TOOLS = {"finish_decision"}
READ_TOOLS = CATALOG_TOOLS | CAPABILITY_TOOLS | {
    "get_colony_state", "inspect_map", "check_build_placements", "check_zone_placement", "finish_decision"
}


@dataclass(frozen=True)
class ToolResultTelemetry:
    tool: str
    raw_chars: int
    model_chars: int
    compression_ratio: float
    success: bool

    def as_log_line(self) -> str:
        return (
            "[TOOL RESULT] "
            f"tool={self.tool} rawChars={self.raw_chars} modelChars={self.model_chars} "
            f"compressionRatio={self.compression_ratio:.1f}x success={str(self.success).lower()}"
        )


class ModelToolResultFormatter:
    def __init__(
        self,
        *,
        max_result_chars: int = DEFAULT_MAX_MODEL_RESULT_CHARS,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.max_result_chars = max(1000, max_result_chars)
        self.log = logger or print

    def format(
        self,
        tool_name: str,
        raw_result: dict[str, Any],
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model_result, _ = self.format_with_telemetry(tool_name, raw_result, arguments)
        return model_result

    def format_with_telemetry(
        self,
        tool_name: str,
        raw_result: dict[str, Any],
        arguments: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], ToolResultTelemetry]:
        raw_snapshot = copy.deepcopy(raw_result)
        args_snapshot = copy.deepcopy(arguments) if isinstance(arguments, dict) else {}
        try:
            model_result = self._format(tool_name, raw_snapshot, args_snapshot)
            model_result = self._enforce_bound(tool_name, model_result)
        except Exception as exc:
            self.log(f"[ERROR] Tool-result formatter failed for {tool_name}: {exc}")
            model_result = conservative_fallback(raw_snapshot, exc)

        raw_chars = serialized_chars(raw_result)
        model_chars = serialized_chars(model_result)
        success = result_success(model_result)
        telemetry = ToolResultTelemetry(
            tool=tool_name,
            raw_chars=raw_chars,
            model_chars=model_chars,
            compression_ratio=(raw_chars / model_chars) if model_chars else 0.0,
            success=success,
        )
        return model_result, telemetry

    def _format(self, tool_name: str, raw: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise TypeError("Raw tool result must be an object")
        if raw.get("dryRun") is True:
            result = {
                "dryRun": True,
                "proposed": bool(raw.get("proposed")),
                "executed": False,
                "duplicateProposal": bool(raw.get("duplicateProposal")),
                "summary": raw.get("proposal"),
            }
            if tool_name == "place_blueprints":
                placements = arguments.get("placements")
                result["wouldPlace"] = len(placements) if isinstance(placements, list) else 0
            elif tool_name == "set_allowed_area_cells":
                cells = arguments.get("cells")
                result["wouldTouchCells"] = len(cells) if isinstance(cells, list) else 0
                result["areaId"] = arguments.get("area_id")
                result["allowed"] = arguments.get("allowed")
            else:
                result["wouldSubmit"] = copy.deepcopy(raw.get("wouldSubmit"))
            return drop_nulls(result)
        if tool_name in READ_TOOLS:
            return self._format_read(tool_name, raw, arguments)
        return self._format_command(tool_name, raw, arguments)

    def _format_read(self, tool_name: str, raw: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name in CAPABILITY_TOOLS and raw.get("success") is False:
            return drop_nulls({
                "success": False,
                "reason": raw.get("reason") or raw.get("error") or "Capability operation failed",
                "availableCapabilities": copy.deepcopy(raw.get("availableCapabilities")),
                "maxDynamicGroups": raw.get("maxDynamicGroups"),
                "activeGroups": copy.deepcopy(raw.get("activeGroups")),
            })
        if raw.get("success") is False:
            return failure_result(raw, arguments)
        result = raw.get("result") if raw.get("success") is True and isinstance(raw.get("result"), dict) else raw
        if not isinstance(result, dict):
            raise TypeError("Read tool did not return an object")
        if result.get("error") or result.get("gameLoaded") is False:
            return failure_result(result, arguments)
        if tool_name in CAPABILITY_TOOLS:
            return {"success": True, **drop_nulls(copy.deepcopy(result))}
        if tool_name in TERMINAL_TOOLS:
            return {"success": True, **drop_nulls(copy.deepcopy(result))}
        if tool_name == "get_colony_state":
            return {"success": True, **drop_nulls(copy.deepcopy(result))}
        if tool_name == "inspect_map":
            return compact_inspect_map(result)
        if tool_name == "check_build_placements":
            return compact_build_validation(result, arguments)
        if tool_name == "check_zone_placement":
            return compact_zone_validation(result)
        if tool_name in CATALOG_TOOLS:
            return compact_catalog(tool_name, result)
        raise ValueError(f"No formatter for read tool {tool_name}")

    def _format_command(self, tool_name: str, raw: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        status = raw.get("status")
        if raw.get("success") is False:
            return failure_result(raw, arguments)
        if status != "completed":
            result: dict[str, Any] = {
                "success": None,
                "status": status or "unknown",
                "commandId": raw.get("commandId"),
                "uncertain": bool(raw.get("uncertain", True)),
            }
            reason = raw.get("error") or raw.get("message")
            if reason:
                result["reason"] = short_text(reason)
            return drop_nulls(result)
        if raw.get("success") is not True:
            return failure_result(raw, arguments)
        if tool_name == "place_blueprints":
            return compact_blueprint_batch(raw, arguments)
        result = {"success": True}
        result.update(command_semantics(tool_name, arguments, raw.get("data")))
        return drop_nulls(result)

    def _enforce_bound(self, tool_name: str, result: dict[str, Any]) -> dict[str, Any]:
        limit = MAX_INSPECT_MAP_CHARS if tool_name == "inspect_map" else self.max_result_chars
        if serialized_chars(result) <= limit:
            return result
        if tool_name == "inspect_map":
            return bound_inspect_map(result, limit)
        if tool_name in CATALOG_TOOLS:
            return bound_catalog(result, limit)
        if tool_name in ("check_build_placements", "check_zone_placement", "place_blueprints"):
            return bound_exception_list(result, limit)
        if tool_name == "get_colony_state":
            # Milestone 5 already applies a semantic section bound. If an
            # unexpected wrapper pushes it over, preserve metadata and ask for
            # a narrower follow-up rather than destructively slicing state.
            return {
                "success": False,
                "reason": "Colony-state section exceeded the model result bound",
                "section": result.get("section"),
                "snapshotVersion": result.get("snapshotVersion"),
                "truncated": True,
            }
        return {
            "success": False,
            "reason": "Tool result exceeded the model result bound",
            "truncated": True,
        }


def compact_inspect_map(raw: dict[str, Any]) -> dict[str, Any]:
    palette: list[dict[str, Any]] = []
    palette_index: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for row in dict_list(raw.get("terrainRows")):
        runs: list[list[int]] = []
        for run in dict_list(row.get("runs")):
            cell = compact_cell(run.get("cell"))
            key = json.dumps(cell, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            index = palette_index.get(key)
            if index is None:
                index = len(palette)
                palette_index[key] = index
                palette.append({"id": index, **cell})
            runs.append([safe_int(run.get("x")), safe_int(run.get("len")), index])
        rows.append({"z": safe_int(row.get("z")), "runs": runs})

    things: list[dict[str, Any]] = []
    plant_groups: dict[str, dict[str, Any]] = {}
    raw_things = dict_list(raw.get("things"))
    for thing in raw_things:
        if "growth" in thing or thing.get("type") == "plant":
            group_plant(plant_groups, thing)
        else:
            things.append(compact_map_thing(thing))

    zones = [
        drop_nulls(select_fields(zone, ("id", "type", "label", "cellCount", "bounds", "plantDef", "priority", "preset")))
        for zone in dict_list(raw.get("zones"))
    ]
    result = {
        "success": True,
        "mapId": raw.get("mapId"),
        "bounds": copy.deepcopy(raw.get("bounds")),
        "encoding": {
            "terrainRuns": "Each run is [xStart,length,terrainPaletteId] on the row z.",
            "plantCells": "Each plant cell is [x,z]; growth micro-values are omitted.",
        },
        "terrainPalette": palette,
        "terrainRows": rows,
        "things": sorted(things, key=map_thing_priority),
        "plantGroups": [plant_groups[key] for key in sorted(plant_groups)],
        "zones": zones,
        "truncated": bool(raw.get("truncated")) or len(raw_things) >= 240,
    }
    return drop_nulls(result)


def compact_cell(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"unknown": True}
    if value.get("fog") is True:
        return {"fog": True}
    result = {
        "terrain": value.get("terrain"),
        "label": value.get("terrainLabel"),
        "fertility": value.get("fertility"),
        "walkable": value.get("walkable"),
        "buildable": value.get("buildable"),
        "roofed": value.get("roofed"),
        "room": copy.deepcopy(value.get("room")),
        "adjacentRoomIds": copy.deepcopy(value.get("adjacentRoomIds")),
        "water": value.get("water"),
        "growingZone": value.get("canCreateGrowingZone"),
        "stockpileZone": value.get("canCreateStockpile"),
        "affordances": copy.deepcopy(value.get("affordances")),
    }
    return drop_nulls(result)


def decode_inspect_map(compact: dict[str, Any]) -> dict[tuple[int, int], dict[str, Any]]:
    """Test/debug helper that reconstructs model-visible terrain cell facts."""
    palette = {safe_int(item.get("id")): {key: copy.deepcopy(value) for key, value in item.items() if key != "id"} for item in dict_list(compact.get("terrainPalette"))}
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for row in dict_list(compact.get("terrainRows")):
        z = safe_int(row.get("z"))
        for run in row.get("runs", []) if isinstance(row.get("runs"), list) else []:
            if not isinstance(run, list) or len(run) != 3:
                continue
            x_start, length, palette_id = (safe_int(item) for item in run)
            for x in range(x_start, x_start + length):
                cells[(x, z)] = copy.deepcopy(palette.get(palette_id, {"unknown": True}))
    return cells


def compact_map_thing(thing: dict[str, Any]) -> dict[str, Any]:
    position = thing.get("position") if isinstance(thing.get("position"), dict) else {}
    result: dict[str, Any] = {
        "id": thing.get("id"),
        "type": thing.get("type"),
        "def": thing.get("defName"),
        "label": thing.get("label"),
        "x": position.get("x"),
        "z": position.get("z"),
        "rotation": thing.get("rotation"),
        "room": copy.deepcopy(thing.get("room")),
    }
    size = thing.get("size")
    if isinstance(size, dict) and (safe_int(size.get("x")) != 1 or safe_int(size.get("z")) != 1):
        result["size"] = copy.deepcopy(size)
    if safe_int(thing.get("stackCount")) > 1:
        result["count"] = safe_int(thing.get("stackCount"))
    if thing.get("powered") is not None:
        result["powered"] = thing.get("powered")
    if thing.get("forbidden") is True:
        result["forbidden"] = True
    hit_points = safe_int(thing.get("hitPoints"))
    max_hit_points = safe_int(thing.get("maxHitPoints"))
    if max_hit_points > 0 and hit_points < max_hit_points:
        result["health"] = [hit_points, max_hit_points]
    return drop_nulls(result)


def group_plant(groups: dict[str, dict[str, Any]], plant: dict[str, Any]) -> None:
    position = plant.get("position") if isinstance(plant.get("position"), dict) else {}
    properties = {
        "def": plant.get("defName"),
        "label": plant.get("label"),
        "mature": bool(plant.get("mature")),
        "harvestable": bool(plant.get("harvestableNow")),
        "canCut": bool(plant.get("canDesignateCut")),
        "canHarvest": bool(plant.get("canDesignateHarvest")),
        "forbidden": bool(plant.get("forbidden")),
    }
    key = json.dumps(properties, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if key not in groups:
        groups[key] = {**drop_nulls(properties), "cells": []}
    groups[key]["cells"].append([safe_int(position.get("x")), safe_int(position.get("z"))])


def map_thing_priority(thing: dict[str, Any]) -> tuple[int, str, str]:
    kind = str(thing.get("type") or "")
    priorities = {"hostilePawn": 0, "playerPawn": 0, "building": 1, "blueprint": 1, "frame": 1, "mineable": 2}
    return priorities.get(kind, 3), str(thing.get("def") or ""), str(thing.get("id") or "")


def compact_build_validation(raw: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    placements = arguments.get("placements") if isinstance(arguments.get("placements"), list) else []
    checked = dict_list(raw.get("placements"))
    invalid = []
    valid_count = 0
    for index, item in enumerate(checked):
        if item.get("valid") is True:
            valid_count += 1
            continue
        source = placements[index] if index < len(placements) and isinstance(placements[index], dict) else {}
        invalid.append(drop_nulls({
            "index": item.get("index", index),
            "x": source.get("x"),
            "z": source.get("z"),
            "buildDef": source.get("build_def", source.get("buildDef")),
            "reason": short_text(item.get("reason") or "Placement is invalid"),
            "requiredTerrainAffordance": item.get("requiredTerrainAffordance"),
        }))
    requested = len(placements) or len(checked)
    return {
        "success": len(invalid) == 0 and valid_count == requested,
        "valid": len(invalid) == 0 and valid_count == requested,
        "requested": requested,
        "validCount": valid_count,
        "invalid": invalid,
    }


def compact_zone_validation(raw: dict[str, Any]) -> dict[str, Any]:
    invalid = [
        drop_nulls({"x": item.get("x"), "z": item.get("z"), "reason": short_text(item.get("reason") or "Cell is invalid")})
        for item in dict_list(raw.get("cells"))
        if item.get("valid") is not True
    ]
    requested = safe_int(raw.get("requestedCells"))
    valid_count = safe_int(raw.get("validCells"))
    invalid_count = safe_int(raw.get("invalidCells"))
    valid = requested > 0 and invalid_count == 0 and valid_count == requested
    return drop_nulls({
        "success": valid,
        "valid": valid,
        "zoneType": raw.get("zoneType"),
        "requestedBounds": copy.deepcopy(raw.get("requestedBounds")),
        "requestedCells": requested,
        "validCells": valid_count,
        "invalidCount": invalid_count,
        "invalid": invalid,
        "invalidReasons": copy.deepcopy(raw.get("invalidReasons")) if invalid else None,
        "truncated": safe_int(raw.get("cellLimit")) < requested,
    })


def compact_catalog(tool_name: str, raw: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "list_build_options":
        entries = [drop_nulls(select_fields(item, ("defName", "label", "category", "size", "stuffable", "available", "researchSatisfied", "requiredTerrainAffordance", "footprint", "cost"))) for item in dict_list(raw.get("options"))]
        return {"success": True, "options": entries, "truncated": bool(raw.get("truncated")) or len(entries) >= 80}
    if tool_name == "get_build_info":
        return {"success": True, "buildable": drop_nulls(copy.deepcopy(raw.get("buildable", {})))}
    if tool_name == "list_growable_plants":
        entries = [drop_nulls(select_fields(item, ("defName", "label", "fertilityMin", "sowMinSkill", "growthSeasonNow"))) for item in dict_list(raw.get("plants"))]
        return {"success": True, "plants": entries, "truncated": bool(raw.get("truncated")) or len(entries) >= 120}
    if tool_name == "list_recipes":
        entries = [drop_nulls(select_fields(item, ("recipeDef", "label", "currentlyAvailable", "workSkill", "ingredients", "products", "skillRequirements"))) for item in dict_list(raw.get("recipes"))]
        return drop_nulls({"success": True, "worktableId": raw.get("worktableId"), "worktableDef": raw.get("worktableDef"), "recipes": entries, "truncated": bool(raw.get("truncated")) or len(entries) >= 160})
    raise ValueError(f"Unknown catalog tool {tool_name}")


def compact_blueprint_batch(raw: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    requested_placements = arguments.get("placements") if isinstance(arguments.get("placements"), list) else []
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    outcomes = dict_list(data.get("placements"))
    failed = []
    placed = 0
    for index, outcome in enumerate(outcomes):
        if outcome.get("success") is True:
            placed += 1
            continue
        source = requested_placements[index] if index < len(requested_placements) and isinstance(requested_placements[index], dict) else {}
        failed.append(drop_nulls({
            "index": outcome.get("index", index),
            "x": source.get("x"),
            "z": source.get("z"),
            "buildDef": source.get("build_def", source.get("buildDef")),
            "reason": short_text(outcome.get("message") or "Blueprint was not placed"),
        }))
    requested = len(requested_placements) or len(outcomes)
    if not outcomes and requested:
        return {"success": False, "requested": requested, "placed": 0, "failed": [], "reason": "Bridge returned no per-placement outcomes"}
    return {"success": len(failed) == 0 and placed == requested, "requested": requested, "placed": placed, "failed": failed}


def command_semantics(tool_name: str, arguments: dict[str, Any], data: Any) -> dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    field_maps: dict[str, tuple[tuple[str, str], ...]] = {
        "set_speed": (("speed", "speed"),),
        "draft": (("pawn_id", "pawnId"),), "undraft": (("pawn_id", "pawnId"),),
        "move": (("pawn_id", "pawnId"), ("x", "x"), ("z", "z")),
        "set_work_priority": (("pawn_id", "pawnId"), ("work_type", "workType"), ("priority", "priority")),
        "allow": (("thing_id", "thingId"),), "forbid": (("thing_id", "thingId"),),
        "set_research": (("research_def", "researchDef"),),
        "prioritize_job": (("pawn_id", "pawnId"), ("target_id", "targetId")),
        "designate_mine": (("x", "x"), ("z", "z")), "designate_cut": (("x", "x"), ("z", "z")),
        "designate_harvest": (("x", "x"), ("z", "z")), "designate_hunt": (("thing_id", "thingId"),),
        "set_stockpile_priority": (("zone_id", "zoneId"), ("priority", "priority")),
        "set_stockpile_preset": (("zone_id", "zoneId"), ("preset", "preset")),
        "set_growing_zone_plant": (("zone_id", "zoneId"), ("plant_def", "plantDef")),
        "cancel_at": (("x", "x"), ("z", "z")),
        "designate_deconstruct": (("thing_id", "thingId"),),
        "equip_weapon": (("pawn_id", "pawnId"), ("thing_id", "primaryEquipmentId")),
        "drop_primary_weapon": (("pawn_id", "pawnId"),),
        "wear_apparel": (("pawn_id", "pawnId"), ("thing_id", "apparelId")),
        "remove_apparel": (("pawn_id", "pawnId"), ("thing_id", "apparelId")),
        "assign_bed": (("pawn_id", "pawnId"), ("bed_id", "bedId")),
        "unassign_bed": (("pawn_id", "pawnId"),),
        "set_bill_suspended": (("worktable_id", "worktableId"), ("bill_id", "billId"), ("suspended", "suspended")),
        "remove_bill": (("worktable_id", "worktableId"), ("bill_id", "billId")),
        "set_bill_target_count": (("worktable_id", "worktableId"), ("bill_id", "billId"), ("target_count", "targetCount")),
        "set_power_switch": (("thing_id", "thingId"), ("on", "on")),
        "set_target_fuel_level": (("thing_id", "thingId"), ("level", "targetFuelLevel")),
        "assign_allowed_area": (("pawn_id", "pawnId"), ("area_id", "areaId")),
        "prioritize_haul": (("pawn_id", "pawnId"), ("thing_id", "targetId")),
        "prioritize_rescue": (("pawn_id", "pawnId"), ("target_pawn_id", "targetPawnId")),
        "prioritize_tend": (("pawn_id", "pawnId"), ("target_pawn_id", "targetPawnId")),
        "prioritize_clean": (("pawn_id", "pawnId"), ("x", "x"), ("z", "z")),
        "prioritize_refuel": (("pawn_id", "pawnId"), ("thing_id", "targetId")),
        "prioritize_construct": (("pawn_id", "pawnId"), ("blueprint_or_frame_id", "targetId")),
    }
    result = {output: arguments.get(source) for source, output in field_maps.get(tool_name, ())}
    if tool_name in ("draft", "undraft"):
        result["drafted"] = tool_name == "draft"
    if tool_name in ("allow", "forbid"):
        result["forbidden"] = tool_name == "forbid"
    if tool_name in ("create_stockpile", "create_growing_zone"):
        result.update({"zoneId": payload.get("zoneId"), "createdCells": payload.get("cellsAdded")})
        result["bounds"] = rect_from_arguments(arguments)
        if tool_name == "create_growing_zone":
            result["plantDef"] = payload.get("plantDef")
    elif tool_name == "place_blueprint":
        result.update({"blueprintId": payload.get("blueprintId"), "buildDef": arguments.get("build_def"), "x": arguments.get("x"), "z": arguments.get("z")})
    elif tool_name == "place_blueprints":
        placements = arguments.get("placements") if isinstance(arguments.get("placements"), list) else []
        result["requested"] = len(placements)
    elif tool_name == "add_bill":
        result.update({"worktableId": arguments.get("worktable_id"), "billId": payload.get("billId"), "recipe": arguments.get("recipe_def"), "repeatMode": arguments.get("repeat_mode"), "targetCount": arguments.get("target_count")})
    elif tool_name == "create_allowed_area":
        result.update({"areaId": payload.get("areaId"), "label": arguments.get("label")})
    elif tool_name == "set_allowed_area_cells":
        cells = arguments.get("cells") if isinstance(arguments.get("cells"), list) else []
        result.update({"areaId": arguments.get("area_id"), "requestedCells": len(cells), "cellsTouched": payload.get("cellsTouched"), "allowed": arguments.get("allowed")})
    elif not result and payload:
        result.update(drop_nulls(copy.deepcopy(payload)))
    return drop_nulls(result)


def failure_result(raw: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    reason = raw.get("error") or raw.get("reason") or raw.get("message") or "Tool failed"
    result: dict[str, Any] = {"success": False, "reason": short_text(reason)}
    for source, output in (
        ("pawn_id", "pawnId"), ("thing_id", "thingId"), ("target_id", "targetId"),
        ("target_pawn_id", "targetPawnId"), ("worktable_id", "worktableId"),
        ("bill_id", "billId"), ("bed_id", "bedId"), ("zone_id", "zoneId"),
        ("area_id", "areaId"), ("build_def", "buildDef"), ("recipe_def", "recipeDef"),
        ("work_type", "workType"), ("x", "x"), ("z", "z"),
    ):
        if source in arguments:
            result[output] = copy.deepcopy(arguments[source])
    if raw.get("commandId") and raw.get("status") != "completed":
        result["commandId"] = raw.get("commandId")
    if raw.get("requiredCapability"):
        result["requiredCapability"] = raw.get("requiredCapability")
    if raw.get("availableCapabilities"):
        result["availableCapabilities"] = copy.deepcopy(raw.get("availableCapabilities"))
    for field in ("maxWidth", "maxHeight", "requestedWidth", "requestedHeight"):
        if raw.get(field) is not None:
            result[field] = raw.get(field)
    return drop_nulls(result)


def bound_inspect_map(result: dict[str, Any], limit: int) -> dict[str, Any]:
    bounded = copy.deepcopy(result)
    bounded["truncated"] = True
    for field in ("plantGroups", "things", "zones"):
        entries = bounded.get(field)
        if not isinstance(entries, list):
            continue
        original = len(entries)
        while entries and serialized_chars(bounded) > limit:
            entries.pop()
        if len(entries) < original:
            bounded[f"{field}Omitted"] = original - len(entries)
    if serialized_chars(bounded) <= limit:
        return bounded
    bounds = copy.deepcopy(bounded.get("bounds"))
    return {
        "success": False,
        "reason": "Exact terrain encoding exceeds the model result bound; inspect a smaller region",
        "bounds": bounds,
        "truncated": True,
    }


def bound_catalog(result: dict[str, Any], limit: int) -> dict[str, Any]:
    bounded = copy.deepcopy(result)
    list_field = next((field for field in ("options", "plants", "recipes") if isinstance(bounded.get(field), list)), None)
    if list_field is None:
        return {"success": False, "reason": "Catalog result exceeded the model result bound", "truncated": True}
    entries = bounded[list_field]
    original = len(entries)
    while entries and serialized_chars(bounded) > limit:
        entries.pop()
    bounded["truncated"] = True
    bounded["omittedCount"] = original - len(entries)
    return bounded


def bound_exception_list(result: dict[str, Any], limit: int) -> dict[str, Any]:
    bounded = copy.deepcopy(result)
    field = "failed" if isinstance(bounded.get("failed"), list) else "invalid"
    entries = bounded.get(field)
    if not isinstance(entries, list):
        return {"success": False, "reason": "Tool result exceeded the model result bound", "truncated": True}
    original = len(entries)
    while entries and serialized_chars(bounded) > limit:
        entries.pop()
    bounded["truncated"] = True
    bounded[f"{field}Omitted"] = original - len(entries)
    return bounded


def conservative_fallback(raw: Any, exc: Exception) -> dict[str, Any]:
    original_reason = None
    if isinstance(raw, dict):
        original_reason = raw.get("error") or raw.get("reason") or raw.get("message")
    return drop_nulls({
        "success": False,
        "reason": "Tool result could not be safely formatted",
        "originalFailure": result_success(raw) is False,
        "originalReason": short_text(original_reason) if original_reason else None,
        "formatterError": short_text(str(exc)),
        "truncated": True,
    })


def result_success(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("success") is True:
        return True
    if value.get("valid") is True:
        return True
    if value.get("dryRun") is True and value.get("executed") is False:
        return True
    return False


def rect_from_arguments(arguments: dict[str, Any]) -> dict[str, Any] | None:
    fields = (("min_x", "minX"), ("min_z", "minZ"), ("max_x", "maxX"), ("max_z", "maxZ"))
    if not all(source in arguments for source, _ in fields):
        return None
    return {output: arguments[source] for source, output in fields}


def select_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {field: copy.deepcopy(value[field]) for field in fields if field in value}


def drop_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): drop_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [drop_nulls(item) for item in value]
    return value


def dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def short_text(value: Any, limit: int = MAX_ERROR_CHARS) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def serialized_chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=True, default=str))
