"""Deterministic, bounded strategic memory for one RimGPT colony."""

from __future__ import annotations

import copy
import re
from typing import Any

from context_telemetry import estimate_tokens, serialized_chars


MEMORY_FORMAT_VERSION = 1
DEFAULT_MAX_MEMORY_CHARS = 9_000
MAX_ITEM_CHARS = 240
MAX_ASSESSMENT_CHARS = 600
MAX_ROLES_PER_PAWN = 4
MAX_PAWN_ROLES = 24

LIST_LIMITS = {
    "currentGoals": 8,
    "nextPriorities": 10,
    "longTermGoals": 8,
    "decisions": 24,
    "unresolvedProblems": 15,
    "importantLocations": 12,
}
TEXT_LIST_FIELDS = (
    "currentGoals",
    "nextPriorities",
    "longTermGoals",
    "decisions",
    "unresolvedProblems",
)


class StrategicMemoryError(ValueError):
    pass


def empty_memory(colony_identity: dict[str, str]) -> dict[str, Any]:
    return {
        "version": MEMORY_FORMAT_VERSION,
        "colonyIdentity": copy.deepcopy(colony_identity),
        "currentGoals": [],
        "nextPriorities": [],
        "longTermGoals": [],
        "decisions": [],
        "unresolvedProblems": [],
        "importantLocations": [],
        "pawnRoles": {},
        "lastAssessment": "",
    }


def validate_memory(document: Any, colony_identity: dict[str, str]) -> dict[str, Any]:
    """Validate persisted memory and return a bounded canonical copy."""
    if not isinstance(document, dict):
        raise StrategicMemoryError("memory document must be a JSON object")
    if document.get("version") != MEMORY_FORMAT_VERSION:
        raise StrategicMemoryError("unsupported memory version")
    if document.get("colonyIdentity") != colony_identity:
        raise StrategicMemoryError("memory belongs to a different colony")
    for field in TEXT_LIST_FIELDS:
        if not isinstance(document.get(field), list) or not all(isinstance(item, str) for item in document[field]):
            raise StrategicMemoryError(f"{field} must be a list of strings")
    if not isinstance(document.get("importantLocations"), list):
        raise StrategicMemoryError("importantLocations must be a list")
    if not isinstance(document.get("pawnRoles"), dict):
        raise StrategicMemoryError("pawnRoles must be an object")
    if not isinstance(document.get("lastAssessment"), str):
        raise StrategicMemoryError("lastAssessment must be a string")
    for location in document["importantLocations"]:
        _validate_location(location)
    for pawn_id, role_data in document["pawnRoles"].items():
        _validate_role_entry(pawn_id, role_data)
    return _enforce_bounds(copy.deepcopy(document))[0]


def apply_update(
    memory: dict[str, Any],
    update: dict[str, Any],
    colony_identity: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    """Apply an additive strategic patch, returning a new document and changes."""
    if not isinstance(update, dict):
        raise StrategicMemoryError("memory update must be a JSON object")
    result = validate_memory(memory, colony_identity)
    changes: list[str] = []
    bounded = False

    resolved_decisions = _update_strings(update, "resolvedDecisions")
    resolved_problems = _update_strings(update, "resolvedProblems")
    if _remove_normalized(result["decisions"], resolved_decisions):
        changes.append("decisionsResolved")
    if _remove_normalized(result["unresolvedProblems"], resolved_problems):
        changes.append("problemsResolved")

    additions = {
        "currentGoals": _update_strings(update, "currentGoals"),
        "nextPriorities": _update_strings(update, "nextPriorities"),
        "longTermGoals": _update_strings(update, "longTermGoals"),
        "decisions": _update_strings(update, "decisionsToRemember"),
        "unresolvedProblems": _update_strings(update, "unresolvedProblems"),
    }
    for field, values in additions.items():
        unbounded = _merge_texts(values, result[field], 1_000_000)
        merged = unbounded[:LIST_LIMITS[field]]
        bounded = bounded or len(unbounded) > LIST_LIMITS[field]
        if merged != result[field]:
            result[field] = merged
            changes.append(field)

    if "importantLocations" in update:
        locations = update["importantLocations"]
        if not isinstance(locations, list):
            raise StrategicMemoryError("importantLocations update must be a list")
        for location in locations:
            _validate_location(location)
        unbounded_locations = _merge_locations(locations, result["importantLocations"], None)
        merged_locations = unbounded_locations[:LIST_LIMITS["importantLocations"]]
        bounded = bounded or len(unbounded_locations) > LIST_LIMITS["importantLocations"]
        if merged_locations != result["importantLocations"]:
            result["importantLocations"] = merged_locations
            changes.append("importantLocations")

    if "pawnRoles" in update:
        roles = update["pawnRoles"]
        if not isinstance(roles, dict):
            raise StrategicMemoryError("pawnRoles update must be an object")
        existing_role_ids = set(result["pawnRoles"])
        incoming_role_ids = {str(pawn_id) for pawn_id in roles}
        for pawn_id, role_data in roles.items():
            _validate_role_entry(pawn_id, role_data)
            existing = result["pawnRoles"].get(str(pawn_id), {"roles": []})
            incoming = _clean_role_entry(pawn_id, role_data)["roles"]
            bounded = bounded or len(_merge_texts(incoming, existing["roles"], 1_000_000)) > MAX_ROLES_PER_PAWN
        merged_roles = _merge_pawn_roles(roles, result["pawnRoles"])
        if merged_roles != result["pawnRoles"]:
            result["pawnRoles"] = merged_roles
            changes.append("pawnRoles")
        bounded = bounded or len(existing_role_ids | incoming_role_ids) > MAX_PAWN_ROLES

    if "assessment" in update:
        assessment = update["assessment"]
        if not isinstance(assessment, str):
            raise StrategicMemoryError("assessment must be a string")
        assessment = _clean_text(assessment, MAX_ASSESSMENT_CHARS)
        if assessment != result["lastAssessment"]:
            result["lastAssessment"] = assessment
            changes.append("lastAssessment")

    result, truncated = _enforce_bounds(result)
    if truncated or bounded:
        changes.append("bounded")
    return result, changes


def reconcile_memory(
    memory: dict[str, Any],
    state: dict[str, Any] | None,
    colony_identity: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    """Remove only strategic entries that current authoritative state proves obsolete."""
    result = validate_memory(memory, colony_identity)
    if not isinstance(state, dict):
        return result, []
    changes: list[str] = []

    pawn_ids = {
        str(pawn.get("id"))
        for pawn in _dict_list(state.get("colonists"))
        if pawn.get("id") is not None
    }
    obsolete_roles = sorted(set(result["pawnRoles"]) - pawn_ids)
    if obsolete_roles:
        for pawn_id in obsolete_roles:
            del result["pawnRoles"][pawn_id]
        changes.append("pawnRolesRemoved")

    resolved = _provably_resolved_items(state)
    for field in ("currentGoals", "nextPriorities", "unresolvedProblems"):
        before = result[field]
        result[field] = [item for item in before if normalize_text(item) not in resolved]
        if result[field] != before:
            changes.append(field + "Reconciled")
    return result, changes


def memory_telemetry(memory: dict[str, Any]) -> dict[str, int]:
    return {
        "chars": serialized_chars(memory),
        "approxTokens": estimate_tokens(serialized_chars(memory)),
        "currentGoals": len(memory.get("currentGoals", [])),
        "nextPriorities": len(memory.get("nextPriorities", [])),
        "longTermGoals": len(memory.get("longTermGoals", [])),
        "decisions": len(memory.get("decisions", [])),
        "unresolvedProblems": len(memory.get("unresolvedProblems", [])),
        "locations": len(memory.get("importantLocations", [])),
        "pawnRoles": len(memory.get("pawnRoles", {})),
    }


def normalize_text(value: str) -> str:
    value = re.sub(r"[^\w\s]", " ", value.casefold())
    return re.sub(r"\s+", " ", value).strip()


def _update_strings(update: dict[str, Any], field: str) -> list[str]:
    if field not in update:
        return []
    value = update[field]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise StrategicMemoryError(f"{field} update must be a list of strings")
    return [_clean_text(item, MAX_ITEM_CHARS) for item in value if _clean_text(item, MAX_ITEM_CHARS)]


def _merge_texts(incoming: list[str], existing: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in incoming + existing:
        normalized = normalize_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _remove_normalized(items: list[str], resolved: list[str]) -> bool:
    normalized = {normalize_text(item) for item in resolved}
    before = len(items)
    items[:] = [item for item in items if normalize_text(item) not in normalized]
    return len(items) != before


def _merge_locations(incoming: list[Any], existing: list[Any], limit: int | None = LIST_LIMITS["importantLocations"]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for location in incoming + existing:
        clean = _clean_location(location)
        key = (normalize_text(clean["label"]), clean["x"], clean["z"], clean.get("mapId"))
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if limit is not None and len(result) >= limit:
            break
    return result


def _merge_pawn_roles(incoming: dict[str, Any], existing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {str(pawn_id): _clean_role_entry(pawn_id, value) for pawn_id, value in existing.items()}
    for pawn_id in sorted(incoming):
        _validate_role_entry(pawn_id, incoming[pawn_id])
        current = result.get(str(pawn_id), {"name": "", "roles": []})
        update = _clean_role_entry(pawn_id, incoming[pawn_id])
        result[str(pawn_id)] = {
            "name": update["name"] or current["name"],
            "roles": _merge_texts(update["roles"], current["roles"], MAX_ROLES_PER_PAWN),
        }
    return {pawn_id: result[pawn_id] for pawn_id in sorted(result)[:MAX_PAWN_ROLES]}


def _validate_location(location: Any) -> None:
    if not isinstance(location, dict):
        raise StrategicMemoryError("location must be an object")
    if not isinstance(location.get("label"), str):
        raise StrategicMemoryError("location label must be a string")
    if not isinstance(location.get("x"), int) or not isinstance(location.get("z"), int):
        raise StrategicMemoryError("location x and z must be integers")
    for key in ("mapId", "purpose", "note"):
        if key in location and location[key] is not None and not isinstance(location[key], str):
            raise StrategicMemoryError(f"location {key} must be a string or null")


def _clean_location(location: dict[str, Any]) -> dict[str, Any]:
    result = {
        "label": _clean_text(location["label"], 80),
        "x": location["x"],
        "z": location["z"],
    }
    for key, limit in (("mapId", 128), ("purpose", 160), ("note", 200)):
        value = location.get(key)
        if isinstance(value, str):
            value = _clean_text(value, limit)
            if value:
                result[key] = value
    return result


def _validate_role_entry(pawn_id: Any, role_data: Any) -> None:
    if not isinstance(pawn_id, str) or not pawn_id.strip() or len(pawn_id) > 128:
        raise StrategicMemoryError("pawn role ID must be a bounded stable ID")
    if not isinstance(role_data, dict) or not isinstance(role_data.get("name", ""), str):
        raise StrategicMemoryError("pawn role must include a string name")
    if not isinstance(role_data.get("roles"), list) or not all(isinstance(role, str) for role in role_data["roles"]):
        raise StrategicMemoryError("pawn role roles must be a list of strings")


def _clean_role_entry(pawn_id: Any, role_data: dict[str, Any]) -> dict[str, Any]:
    _validate_role_entry(pawn_id, role_data)
    return {
        "name": _clean_text(role_data.get("name", ""), 100),
        "roles": _merge_texts(
            [_clean_text(role, 80) for role in role_data["roles"]],
            [],
            MAX_ROLES_PER_PAWN,
        ),
    }


def _enforce_bounds(memory: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    result = copy.deepcopy(memory)
    truncated = False
    for field in TEXT_LIST_FIELDS:
        values = [_clean_text(value, MAX_ITEM_CHARS) for value in result[field]]
        bounded = _merge_texts(values, [], LIST_LIMITS[field])
        truncated = truncated or bounded != result[field]
        result[field] = bounded
    locations = _merge_locations(result["importantLocations"], [])
    truncated = truncated or locations != result["importantLocations"]
    result["importantLocations"] = locations
    roles = _merge_pawn_roles({}, result["pawnRoles"])
    truncated = truncated or roles != result["pawnRoles"]
    result["pawnRoles"] = roles
    assessment = _clean_text(result["lastAssessment"], MAX_ASSESSMENT_CHARS)
    truncated = truncated or assessment != result["lastAssessment"]
    result["lastAssessment"] = assessment

    # Keep newest additions (the front of each list) when the total cap applies.
    while serialized_chars(result) > DEFAULT_MAX_MEMORY_CHARS:
        if result["lastAssessment"]:
            result["lastAssessment"] = result["lastAssessment"][:-100].rstrip()
        else:
            field = next((name for name in ("decisions", "longTermGoals", "currentGoals", "unresolvedProblems", "nextPriorities") if result[name]), None)
            if field is not None:
                result[field].pop()
            elif result["importantLocations"]:
                result["importantLocations"].pop()
            elif result["pawnRoles"]:
                result["pawnRoles"].pop(sorted(result["pawnRoles"])[-1])
            else:
                break
        truncated = True
    return result, truncated


def _provably_resolved_items(state: dict[str, Any]) -> set[str]:
    resolved: set[str] = set()
    buildings = _dict_list(state.get("buildings"))
    if any(
        building.get("type") == "building"
        and "researchbench" in str(building.get("defName") or "").casefold()
        for building in buildings
    ):
        resolved.update({normalize_text(item) for item in ("no research bench", "need research bench", "build research bench")})

    colonists = _dict_list(state.get("colonists"))
    if colonists and all(_has_primary_weapon(pawn) for pawn in colonists):
        resolved.update({normalize_text(item) for item in ("colonists lack weapons", "equip colonists")})

    zones = _dict_list(_object_value(state.get("map"), "zones"))
    crops = {"rice": "Plant_Rice", "potato": "Plant_Potato", "corn": "Plant_Corn", "cotton": "Plant_Cotton", "healroot": "Plant_Healroot"}
    for crop, def_name in crops.items():
        if any(zone.get("type") == "growing" and zone.get("plantDef") == def_name and int(zone.get("cellCount") or 0) > 0 for zone in zones):
            resolved.update(
                {
                    normalize_text(f"build {crop} growing zone"),
                    normalize_text(f"create {crop} growing zone"),
                    normalize_text(f"establish {crop} growing zone"),
                }
            )
    return resolved


def _has_primary_weapon(pawn: dict[str, Any]) -> bool:
    equipment = pawn.get("primaryEquipment")
    return isinstance(equipment, dict) and bool(equipment.get("id") or equipment.get("defName"))


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _object_value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _clean_text(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]
