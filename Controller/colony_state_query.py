"""Bounded model-facing views over the latest authoritative StateStore state."""

from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any, Callable

from state_store import StateStore, snapshot_version


VALID_SECTIONS = (
    "pawns",
    "work",
    "resources",
    "research",
    "buildings",
    "zones",
    "equipment",
    "apparel",
    "beds",
    "worktables",
    "bills",
    "power",
    "threats",
    "environment",
)
DEFAULT_MAX_SECTION_CHARS = 12_000
DEFAULT_MAX_ENTRIES = 80
DEFAULT_MAX_STRATEGIC_BUILDINGS = 36


class ColonyStateQueryError(ValueError):
    pass


class ColonyStateQuery:
    """Produces explicitly allowlisted snapshots without exposing JSON traversal."""

    def __init__(
        self,
        state_store: StateStore,
        *,
        max_section_chars: int = DEFAULT_MAX_SECTION_CHARS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.state_store = state_store
        self.max_section_chars = max(1000, max_section_chars)
        self.max_entries = max(1, max_entries)

    def get(self, section: str) -> dict[str, Any]:
        if section not in VALID_SECTIONS:
            raise ColonyStateQueryError(
                f"Unsupported colony-state section '{section}'. Valid sections: {', '.join(VALID_SECTIONS)}"
            )
        state = self.state_store.get_current_state()
        if not isinstance(state, dict):
            raise ColonyStateQueryError("No authoritative RimWorld state is currently available")

        builder: Callable[[dict[str, Any]], Any] = getattr(self, f"_{section}")
        data, truncated = builder(state)
        result = {
            "section": section,
            "snapshotVersion": snapshot_version(state),
            "truncated": truncated,
            "data": data,
        }
        return bound_document(result, self.max_section_chars)

    def _pawns(self, state: dict[str, Any]) -> tuple[Any, bool]:
        pawns = [compact_pawn(item) for item in dict_list(state.get("colonists"))]
        return bounded_list(pawns, self.max_entries)

    def _work(self, state: dict[str, Any]) -> tuple[Any, bool]:
        pawns = []
        for pawn in dict_list(state.get("colonists")):
            work = [select_fields(item, ("defName", "label", "capable", "disabled", "disabledReason", "priority")) for item in dict_list(pawn.get("work"))]
            if not work:
                work = [select_fields(item, ("defName", "priority")) for item in dict_list(pawn.get("workPriorities"))]
            skills = sorted(
                [select_fields(item, ("defName", "level", "passion")) for item in dict_list(pawn.get("skills"))],
                key=lambda item: (-int_value(item.get("level")), str(item.get("defName") or "")),
            )
            pawns.append({"id": pawn.get("id"), "name": pawn.get("name"), "work": work[:40], "roleSkills": skills[:12]})
        return bounded_list(pawns, min(self.max_entries, 40))

    def _resources(self, state: dict[str, Any]) -> tuple[Any, bool]:
        resources = state.get("resources") if isinstance(state.get("resources"), dict) else {}
        data: dict[str, Any] = {}
        for key in ("available", "forbidden", "totalVisible", "stored"):
            if key in resources:
                data[key] = compact_tree(resources[key], max_list=50, max_depth=4)
        if not data:
            data = compact_tree(resources, max_list=50, max_depth=4)
        return data, contains_truncation(data)

    def _research(self, state: dict[str, Any]) -> tuple[Any, bool]:
        research = state.get("research") if isinstance(state.get("research"), dict) else {}
        available, truncated = bounded_list(
            [select_fields(item, ("defName", "label", "progress", "cost")) for item in dict_list(research.get("available"))],
            min(self.max_entries, 40),
        )
        return {"current": compact_tree(research.get("current"), max_list=8, max_depth=3), "available": available}, truncated

    def _buildings(self, state: dict[str, Any]) -> tuple[Any, bool]:
        buildings = dict_list(state.get("buildings"))
        grouped = Counter(str(item.get("defName") or item.get("label") or item.get("type") or "unknown") for item in buildings)
        groups = [{"defName": key, "count": count} for key, count in sorted(grouped.items())]
        details = strategic_buildings(buildings, state)
        details, truncated = bounded_list(details, min(self.max_entries, DEFAULT_MAX_STRATEGIC_BUILDINGS))
        groups, groups_truncated = bounded_list(groups, self.max_entries)
        return {
            "countsByDef": groups,
            "entries": details,
            "total": len(buildings),
            "omittedCompletedCount": max(0, len(buildings) - len(details)),
        }, truncated or groups_truncated

    def _zones(self, state: dict[str, Any]) -> tuple[Any, bool]:
        map_state = state.get("map") if isinstance(state.get("map"), dict) else {}
        operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
        zones = [select_fields(item, ("id", "type", "label", "cellCount", "bounds", "plantDef", "priority", "preset")) for item in dict_list(map_state.get("zones"))]
        areas = [select_fields(item, ("id", "label", "cellCount", "bounds")) for item in dict_list(operations.get("allowedAreas"))]
        zones, zone_truncated = bounded_list(zones, self.max_entries)
        areas, area_truncated = bounded_list(areas, self.max_entries)
        return {"zones": zones, "allowedAreas": areas}, zone_truncated or area_truncated

    def _equipment(self, state: dict[str, Any]) -> tuple[Any, bool]:
        operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
        equipment = operations.get("equipment") if isinstance(operations.get("equipment"), dict) else {}
        assigned = [
            {"pawnId": pawn.get("id"), "pawnName": pawn.get("name"), "primary": compact_item(pawn.get("primaryEquipment"))}
            for pawn in dict_list(state.get("colonists"))
        ]
        available = [compact_item(item, include_position=True) for item in dict_list(equipment.get("availableWeapons"))]
        available, truncated = bounded_list(available, self.max_entries)
        return {"assigned": assigned[:40], "availableWeapons": available}, truncated or len(assigned) > 40

    def _apparel(self, state: dict[str, Any]) -> tuple[Any, bool]:
        operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
        apparel = operations.get("apparel") if isinstance(operations.get("apparel"), dict) else {}
        worn = [
            {
                "pawnId": pawn.get("id"),
                "pawnName": pawn.get("name"),
                "worn": [compact_item(item) for item in dict_list(pawn.get("apparel"))[:20]],
            }
            for pawn in dict_list(state.get("colonists"))[:40]
        ]
        available = [compact_item(item, include_position=True) for item in dict_list(apparel.get("available"))]
        available, truncated = bounded_list(available, self.max_entries)
        return {"wornByPawn": worn, "available": available}, truncated

    def _beds(self, state: dict[str, Any]) -> tuple[Any, bool]:
        operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
        beds = [select_fields(item, ("id", "defName", "label", "position", "medical", "forPrisoners", "owners")) for item in dict_list(operations.get("beds"))]
        return bounded_list(beds, self.max_entries)

    def _worktables(self, state: dict[str, Any]) -> tuple[Any, bool]:
        operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
        building_ids = {str(item.get("id")) for item in dict_list(state.get("buildings")) if item.get("id") is not None}
        tables = []
        filtered = 0
        for item in dict_list(operations.get("worktables")):
            if str(item.get("id")) not in building_ids:
                filtered += 1
                continue
            bills = dict_list(item.get("bills"))
            table = select_fields(item, ("id", "defName", "label", "position", "powered", "fueled", "operational"))
            table["billCount"] = len(bills)
            table["bills"] = [select_fields(bill, ("id", "recipeDef", "label", "repeatMode", "targetCount", "suspended")) for bill in bills[:8]]
            tables.append(table)
        tables, truncated = bounded_list(tables, self.max_entries)
        return {"entries": tables, "filteredNonBuildingBillGivers": filtered}, truncated

    def _bills(self, state: dict[str, Any]) -> tuple[Any, bool]:
        operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
        tables = []
        for item in dict_list(operations.get("worktables")):
            bills = [select_fields(bill, ("id", "recipeDef", "label", "repeatMode", "targetCount", "repeatCount", "suspended")) for bill in dict_list(item.get("bills"))]
            if bills:
                tables.append({"worktableId": item.get("id"), "defName": item.get("defName"), "label": item.get("label"), "bills": bills[:30]})
        return bounded_list(tables, min(self.max_entries, 50))

    def _power(self, state: dict[str, Any]) -> tuple[Any, bool]:
        operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
        power = compact_tree(operations.get("power", {}), max_list=self.max_entries, max_depth=5)
        fuel = [select_fields(item, ("id", "defName", "label", "position", "fuel", "fuelCapacity", "targetFuelLevel", "needsFuel")) for item in dict_list(operations.get("fuel"))]
        fuel, truncated = bounded_list(fuel, self.max_entries)
        return {"power": power, "fuel": fuel}, truncated or contains_truncation(power)

    def _threats(self, state: dict[str, Any]) -> tuple[Any, bool]:
        threats = [select_fields(item, ("id", "type", "defName", "label", "faction", "dangerReason", "position", "downed", "weapon")) for item in dict_list(state.get("threats"))]
        return bounded_list(threats, self.max_entries)

    def _environment(self, state: dict[str, Any]) -> tuple[Any, bool]:
        game = state.get("game") if isinstance(state.get("game"), dict) else {}
        map_state = state.get("map") if isinstance(state.get("map"), dict) else {}
        data = {
            "date": game.get("date"),
            "timeOfDay": game.get("timeOfDay"),
            "mapId": game.get("currentMapId"),
            "mapSize": {"width": map_state.get("width"), "height": map_state.get("height")},
            "environment": compact_tree(map_state.get("environment", {}), max_list=20, max_depth=3),
        }
        return data, False


def compact_pawn(pawn: dict[str, Any]) -> dict[str, Any]:
    health = pawn.get("health") if isinstance(pawn.get("health"), dict) else {}
    needs = pawn.get("needs") if isinstance(pawn.get("needs"), dict) else {}
    return {
        "id": pawn.get("id"),
        "name": pawn.get("name"),
        "kindDef": pawn.get("kindDef"),
        "drafted": pawn.get("drafted"),
        "downed": health.get("downed"),
        "position": compact_tree(pawn.get("position"), max_list=2, max_depth=2),
        "currentJob": select_fields(pawn.get("currentJob"), ("defName", "label")),
        "health": select_fields(health, ("summary", "downed", "dead", "bleedingRate", "pain")),
        "needs": {key: need_band(needs.get(key)) for key in ("mood", "food", "rest", "recreation") if needs.get(key) is not None},
        "primaryEquipment": compact_item(pawn.get("primaryEquipment")),
        "assignedBed": compact_item(pawn.get("assignedBed"), include_position=True),
        "allowedArea": compact_item(pawn.get("allowedArea")),
    }


def compact_item(value: Any, *, include_position: bool = False) -> Any:
    fields = ["id", "defName", "label", "quality", "hitPoints", "maxHitPoints", "forbidden", "reserved", "tainted", "wornBy"]
    if include_position:
        fields.append("position")
    return select_fields(value, tuple(fields)) if isinstance(value, dict) else None


def select_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {field: copy.deepcopy(value[field]) for field in fields if field in value}


def compact_tree(value: Any, *, max_list: int, max_depth: int, _depth: int = 0) -> Any:
    if _depth >= max_depth:
        if isinstance(value, (dict, list)):
            return {"truncated": True}
        return copy.deepcopy(value)
    if isinstance(value, dict):
        return {str(key): compact_tree(value[key], max_list=max_list, max_depth=max_depth, _depth=_depth + 1) for key in sorted(value)}
    if isinstance(value, list):
        items = [compact_tree(item, max_list=max_list, max_depth=max_depth, _depth=_depth + 1) for item in value[:max_list]]
        if len(value) > max_list:
            items.append({"truncated": True, "omittedCount": len(value) - max_list})
        return items
    return copy.deepcopy(value)


def bound_document(document: dict[str, Any], max_chars: int) -> dict[str, Any]:
    result = copy.deepcopy(document)
    if serialized_chars(result) <= max_chars:
        return result
    result["truncated"] = True
    while serialized_chars(result) > max_chars:
        candidates = list_paths(result.get("data"))
        if not candidates:
            result["data"] = {"summary": "Section output exceeded its configured size bound"}
            break
        path = max(candidates, key=lambda item: len(resolve_path(result["data"], item)))
        target = resolve_path(result["data"], path)
        if len(target) <= 1:
            if not path:
                result["data"] = {"truncated": True, "omittedCount": len(target)}
            else:
                replace_path(result["data"], path, {"truncated": True, "omittedCount": len(target)})
        else:
            removed = max(1, len(target) // 3)
            del target[-removed:]
            if target and isinstance(target[-1], dict) and target[-1].get("truncated") is True:
                target[-1]["omittedCount"] = int(target[-1].get("omittedCount") or 0) + removed
            else:
                target.append({"truncated": True, "omittedCount": removed})
    return result


def bounded_list(items: list[Any], limit: int) -> tuple[list[Any], bool]:
    if len(items) <= limit:
        return items, False
    return items[:limit] + [{"truncated": True, "omittedCount": len(items) - limit}], True


def list_paths(value: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    result: list[tuple[Any, ...]] = []
    if isinstance(value, list):
        if value:
            result.append(path)
        for index, item in enumerate(value):
            result.extend(list_paths(item, path + (index,)))
    elif isinstance(value, dict):
        for key, item in value.items():
            result.extend(list_paths(item, path + (key,)))
    return result


def resolve_path(value: Any, path: tuple[Any, ...]) -> Any:
    current = value
    for part in path:
        current = current[part]
    return current


def replace_path(value: Any, path: tuple[Any, ...], replacement: Any) -> None:
    if not path:
        return
    parent = resolve_path(value, path[:-1])
    parent[path[-1]] = replacement


def contains_truncation(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("truncated") is True or any(contains_truncation(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_truncation(item) for item in value)
    return False


def serialized_chars(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=True, default=str))


def dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def building_sort_key(value: dict[str, Any]) -> tuple[int, str, str]:
    construction_type = str(value.get("type") or "")
    identity = str(value.get("defName") or value.get("label") or "").lower()
    important_words = ("bed", "bench", "table", "stove", "generator", "battery", "cooler", "heater", "door")
    if construction_type in ("blueprint", "frame"):
        priority = 0
    elif any(word in identity for word in important_words):
        priority = 1
    else:
        priority = 2
    return priority, str(value.get("defName") or ""), str(value.get("id") or "")


def strategic_buildings(buildings: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep actionable construction and operation-linked buildings, not every wall."""
    by_id = {str(item.get("id")): item for item in buildings if item.get("id") is not None}
    operation_status = building_operation_status(state, set(by_id))
    selected = []
    for item in sorted(buildings, key=building_sort_key):
        item_id = str(item.get("id")) if item.get("id") is not None else ""
        construction = str(item.get("type") or "") in ("blueprint", "frame")
        powered = isinstance(item.get("powered"), bool)
        status = operation_status.get(item_id)
        if not construction and not powered and status is None:
            continue
        entry = select_fields(item, ("id", "type", "defName", "position", "rotation", "stuffDef", "powered"))
        if status:
            entry["operation"] = status
        selected.append(entry)
    return selected


def building_operation_status(state: dict[str, Any], building_ids: set[str]) -> dict[str, dict[str, Any]]:
    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    status: dict[str, dict[str, Any]] = {}

    def add(item: dict[str, Any], kind: str, fields: tuple[str, ...]) -> None:
        item_id = str(item.get("id")) if item.get("id") is not None else ""
        if not item_id or item_id not in building_ids:
            return
        value = {"kind": kind}
        value.update(select_fields(item, fields))
        status[item_id] = value

    for item in dict_list(operations.get("beds")):
        add(item, "bed", ("medical", "forPrisoners", "owners"))
    for item in dict_list(operations.get("worktables")):
        add(item, "worktable", ("powered", "fueled", "operational"))
    for item in dict_list(operations.get("fuel")):
        add(item, "fuel", ("fuel", "fuelCapacity", "needsFuel"))

    power = operations.get("power") if isinstance(operations.get("power"), dict) else {}
    for network in dict_list(power.get("networks")):
        for item in dict_list(network.get("connectedBuildings")):
            add(item, "power", ("connected", "powerOn", "switchedOn", "fueled"))
    for item in dict_list(power.get("unpoweredBuildings")):
        add(item, "power", ("connected", "powerOn", "switchedOn", "fueled"))
    return status


def int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def need_band(value: Any) -> str:
    try:
        level = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if level < 0.15:
        return "critical"
    if level < 0.35:
        return "low"
    if level < 0.75:
        return "adequate"
    return "high"
