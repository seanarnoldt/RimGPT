"""Deterministic, semantic and bounded diffs for authoritative RimGPT state."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Callable

from state_store import identity_from_state, snapshot_ticks, snapshot_version, state_schema_version


DEFAULT_MAX_ENTITY_DETAILS = 40
DEFAULT_MAX_DELTA_CHARS = 24_000


class StateDiff:
    @classmethod
    def compare(
        cls,
        baseline: dict[str, Any] | None,
        current: dict[str, Any] | None,
        *,
        max_entity_details: int = DEFAULT_MAX_ENTITY_DETAILS,
        max_delta_chars: int = DEFAULT_MAX_DELTA_CHARS,
        logger: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        engine = cls(max_entity_details, max_delta_chars, logger)
        return engine._compare(baseline, current)

    def __init__(self, max_entity_details: int, max_delta_chars: int, logger: Callable[[str], None] | None) -> None:
        self.max_details = max(1, max_entity_details)
        self.max_delta_chars = max(1000, max_delta_chars)
        self.log = logger

    def _compare(self, baseline: dict[str, Any] | None, current: dict[str, Any] | None) -> dict[str, Any]:
        invalid = self._validate(baseline, current)
        if invalid is not None:
            return {"bootstrapRequired": True, "reason": invalid}
        assert baseline is not None and current is not None

        changes: dict[str, Any] = {}
        self._put(changes, "game", self._diff_game(baseline.get("game"), current.get("game")))
        self._put(changes, "colony", scalar_object_diff(baseline.get("colony"), current.get("colony")))
        self._put(changes, "resources", self._diff_resources(baseline.get("resources"), current.get("resources")))
        self._put(changes, "colonists", self._diff_colonists(baseline.get("colonists"), current.get("colonists")))
        self._put(changes, "research", self._diff_research(baseline.get("research"), current.get("research")))
        self._put(changes, "threats", self._diff_threats(baseline.get("threats"), current.get("threats")))
        self._put(changes, "mapThings", self._diff_generic_collection(baseline.get("mapThings"), current.get("mapThings"), nested=True))
        self._put(changes, "map", self._diff_map(baseline.get("map"), current.get("map")))
        self._put(changes, "construction", self._diff_construction(baseline.get("buildings"), current.get("buildings")))
        self._put(changes, "plants", self._diff_plants(baseline.get("plants"), current.get("plants")))
        self._put(changes, "operations", self._diff_operations(baseline.get("operations"), current.get("operations")))

        from_snapshot = snapshot_version(baseline)
        to_snapshot = snapshot_version(current)
        before_ticks = snapshot_ticks(baseline)
        after_ticks = snapshot_ticks(current)
        result: dict[str, Any] = {
            "fromSnapshot": from_snapshot,
            "toSnapshot": to_snapshot,
            "elapsedTicks": after_ticks - before_ticks if before_ticks is not None and after_ticks is not None else None,
            "changes": changes,
        }
        return self._bound_final(result)

    def _validate(self, baseline: Any, current: Any) -> str | None:
        if baseline is None:
            return "missingDecisionBaseline"
        if not isinstance(baseline, dict):
            return "invalidDecisionBaseline"
        if not isinstance(current, dict):
            return "missingCurrentState"
        old_identity = identity_from_state(baseline)
        new_identity = identity_from_state(current)
        if old_identity is None or new_identity is None:
            return "missingColonyIdentity"
        if old_identity.key != new_identity.key:
            return "colonyIdentityChanged"
        if state_schema_version(baseline) != state_schema_version(current):
            return "incompatibleStateSchema"
        old_map = object_value(baseline.get("game"), "currentMapId")
        new_map = object_value(current.get("game"), "currentMapId")
        if old_map != new_map:
            return "currentMapChanged"
        old_version = snapshot_version(baseline)
        new_version = snapshot_version(current)
        if old_version is not None and new_version is not None and new_version < old_version:
            return "snapshotLineageChanged"
        return None

    def _diff_game(self, before: Any, after: Any) -> dict[str, Any]:
        return fields_diff(before, after, ("loaded", "paused", "speed"))

    def _diff_resources(self, before: Any, after: Any) -> dict[str, Any]:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return change_value(before, after)
        result: dict[str, Any] = {}
        before_has_views = isinstance(before.get("available"), dict)
        after_has_views = isinstance(after.get("available"), dict)
        skip: set[str] = set()
        if before_has_views and after_has_views:
            skip.update({"silver", "wood", "steel", "plasteel", "components", "advancedComponents", "medicine", "industrialMedicine", "glitterworldMedicine", "food"})
        self._diff_numeric_tree(before, after, result, (), skip)
        return result

    def _diff_numeric_tree(self, before: Any, after: Any, output: dict[str, Any], path: tuple[str, ...], skip: set[str]) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                if not path and key in skip:
                    continue
                self._diff_numeric_tree(before.get(key), after.get(key), output, path + (str(key),), set())
            return
        if semantically_equal(before, after):
            return
        if isinstance(before, (int, float)) and not isinstance(before, bool) and isinstance(after, (int, float)) and not isinstance(after, bool):
            set_nested(output, path, {"from": before, "to": after})
        elif path:
            set_nested(output, path, {"from": compact_value(before), "to": compact_value(after)})

    def _diff_colonists(self, before: Any, after: Any) -> dict[str, Any]:
        old, new = index_by_id(before), index_by_id(after)
        result: dict[str, Any] = {}
        added = [compact_colonist(new[key]) for key in sorted(new.keys() - old.keys())]
        removed = [identity_summary(old[key]) for key in sorted(old.keys() - new.keys())]
        changed = []
        for pawn_id in sorted(old.keys() & new.keys()):
            delta = self._diff_colonist(old[pawn_id], new[pawn_id])
            if delta:
                changed.append(with_id(pawn_id, delta))
        self._bounded_put(result, "added", added)
        self._bounded_put(result, "removed", removed)
        self._bounded_put(result, "changed", changed)
        return result

    def _diff_colonist(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        result = fields_diff(before, after, ("name", "drafted", "currentJob", "mentalState"))
        position = position_change(before.get("position"), after.get("position"), 3 if before.get("drafted") or after.get("drafted") else 15)
        self._put(result, "position", position)
        self._put(result, "needs", needs_diff(before.get("needs"), after.get("needs")))
        self._put(result, "health", health_diff(before.get("health"), after.get("health")))
        self._put(result, "work", keyed_field_diff(before.get("work"), after.get("work"), "defName"))
        if not before.get("work") and not after.get("work"):
            self._put(result, "workPriorities", keyed_field_diff(before.get("workPriorities"), after.get("workPriorities"), "defName"))
        self._put(result, "skills", keyed_field_diff(before.get("skills"), after.get("skills"), "defName"))
        if not semantically_equal(before.get("primaryEquipment"), after.get("primaryEquipment")):
            result["primaryEquipment"] = {"from": equipment_summary(before.get("primaryEquipment")), "to": equipment_summary(after.get("primaryEquipment"))}
        self._put(result, "equipment", self._diff_id_list(before.get("equipment"), after.get("equipment"), equipment_summary))
        self._put(result, "apparel", self._diff_id_list(before.get("apparel"), after.get("apparel"), apparel_summary))
        for field in ("assignedBed", "allowedArea"):
            if not semantically_equal(before.get(field), after.get(field)):
                result[field] = {"from": compact_value(before.get(field)), "to": compact_value(after.get(field))}
        return result

    def _diff_research(self, before: Any, after: Any) -> dict[str, Any]:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return change_value(before, after)
        result: dict[str, Any] = {}
        old_current = before.get("current") if isinstance(before.get("current"), dict) else None
        new_current = after.get("current") if isinstance(after.get("current"), dict) else None
        old_def = object_value(old_current, "defName")
        new_def = object_value(new_current, "defName")
        if old_def != new_def:
            result["activeProject"] = {"from": research_summary(old_current), "to": research_summary(new_current)}
            current_available = {item.get("defName") for item in as_dict_list(after.get("available"))}
            if old_def and old_def not in current_available:
                result["completed"] = research_summary(old_current)
        elif old_current is not None and new_current is not None:
            old_progress = number(old_current.get("progress"))
            new_progress = number(new_current.get("progress"))
            cost = number(new_current.get("cost")) or number(old_current.get("cost"))
            if meaningful_progress(old_progress, new_progress, cost):
                result["progress"] = {"project": new_def, "from": old_progress, "to": new_progress, "cost": cost}
        return result

    def _diff_threats(self, before: Any, after: Any) -> dict[str, Any]:
        old, new = index_by_id(before), index_by_id(after)
        result: dict[str, Any] = {}
        self._bounded_put(result, "added", [compact_threat(new[key]) for key in sorted(new.keys() - old.keys())])
        self._bounded_put(result, "resolved", [identity_summary(old[key]) for key in sorted(old.keys() - new.keys())])
        changed = []
        for thing_id in sorted(old.keys() & new.keys()):
            delta = fields_diff(old[thing_id], new[thing_id], ("downed", "weapon"))
            self._put(delta, "position", position_change(old[thing_id].get("position"), new[thing_id].get("position"), 8))
            if delta:
                changed.append(with_id(thing_id, delta))
        self._bounded_put(result, "changed", changed)
        return result

    def _diff_map(self, before: Any, after: Any) -> dict[str, Any]:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return change_value(before, after)
        result = fields_diff(before, after, ("width", "height", "homeAreaBounds"))
        self._put(result, "colonyCenter", position_change(before.get("colonyCenter"), after.get("colonyCenter"), 10))
        self._put(result, "environment", environment_diff(before.get("environment"), after.get("environment")))
        self._put(result, "pointsOfInterest", self._diff_id_list(before.get("pointsOfInterest"), after.get("pointsOfInterest"), generic_entity_summary))
        self._put(result, "zones", self._diff_zones(before.get("zones"), after.get("zones")))
        return result

    def _diff_zones(self, before: Any, after: Any) -> dict[str, Any]:
        old, new = index_by_id(before), index_by_id(after)
        result: dict[str, Any] = {}
        self._bounded_put(result, "added", [compact_zone(new[key], self.max_details * 2) for key in sorted(new.keys() - old.keys())])
        self._bounded_put(result, "removed", [identity_summary(old[key]) for key in sorted(old.keys() - new.keys())])
        changed = []
        for zone_id in sorted(old.keys() & new.keys()):
            delta = fields_diff(old[zone_id], new[zone_id], ("type", "label", "plantDef", "priority", "preset", "cellCount", "bounds"))
            geometry = cell_geometry_diff(old[zone_id].get("cells"), new[zone_id].get("cells"), self.max_details * 2)
            self._put(delta, "geometry", geometry)
            if delta:
                changed.append(with_id(zone_id, delta))
        self._bounded_put(result, "changed", changed)
        return result

    def _diff_construction(self, before: Any, after: Any) -> dict[str, Any]:
        old, new = index_by_id(before), index_by_id(after)
        removed_ids = set(old) - set(new)
        added_ids = set(new) - set(old)
        transitions = []
        consumed_old: set[str] = set()
        consumed_new: set[str] = set()
        by_key: dict[tuple[Any, ...], list[str]] = {}
        for thing_id in sorted(added_ids):
            by_key.setdefault(construction_key(new[thing_id]), []).append(thing_id)
        for old_id in sorted(removed_ids):
            source = old[old_id]
            candidates = by_key.get(construction_key(source), [])
            candidates = [candidate for candidate in candidates if construction_rank(new[candidate]) > construction_rank(source) and candidate not in consumed_new]
            if len(candidates) == 1:
                new_id = candidates[0]
                consumed_old.add(old_id)
                consumed_new.add(new_id)
                transitions.append({"from": construction_summary(source), "to": construction_summary(new[new_id])})
        remaining_old = {key: value for key, value in old.items() if key not in consumed_old}
        remaining_new = {key: value for key, value in new.items() if key not in consumed_new}
        result = self._diff_indexed_maps(remaining_old, remaining_new, construction_summary)
        self._bounded_put(result, "transitions", transitions)
        return result

    def _diff_plants(self, before: Any, after: Any) -> dict[str, Any]:
        old, new = index_by_id(before), index_by_id(after)
        if not old and not new:
            return {}
        result: dict[str, Any] = {}
        harvestable = []
        status_changes = []
        for plant_id in sorted(old.keys() & new.keys()):
            was = old[plant_id]
            now = new[plant_id]
            if not was.get("harvestableNow") and now.get("harvestableNow"):
                harvestable.append(plant_summary(now))
            delta = fields_diff(was, now, ("harvestableNow", "mature", "dead"))
            if delta:
                status_changes.append(with_id(plant_id, delta))
        self._bounded_put(result, "becameHarvestable", harvestable)
        self._bounded_put(result, "statusChanged", status_changes)
        removed = sorted(old.keys() - new.keys())
        added = sorted(new.keys() - old.keys())
        if removed:
            result["removedCount"] = len(removed)
        if added:
            result["addedCount"] = len(added)
        return result

    def _diff_operations(self, before: Any, after: Any) -> dict[str, Any]:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return change_value(before, after)
        result: dict[str, Any] = {}
        old_equipment = object_value(before.get("equipment"), "availableWeapons")
        new_equipment = object_value(after.get("equipment"), "availableWeapons")
        self._put(result, "equipment", self._diff_id_list(old_equipment, new_equipment, equipment_summary))
        old_apparel = object_value(before.get("apparel"), "available")
        new_apparel = object_value(after.get("apparel"), "available")
        self._put(result, "apparel", self._diff_id_list(old_apparel, new_apparel, apparel_summary))
        self._put(result, "beds", self._diff_id_list(before.get("beds"), after.get("beds"), bed_summary))
        self._put(result, "worktables", self._diff_worktables(before.get("worktables"), after.get("worktables")))
        self._put(result, "power", self._diff_power(before.get("power"), after.get("power")))
        self._put(result, "fuel", self._diff_fuel(before.get("fuel"), after.get("fuel")))
        self._put(result, "allowedAreas", self._diff_zones(before.get("allowedAreas"), after.get("allowedAreas")))
        return result

    def _diff_worktables(self, before: Any, after: Any) -> dict[str, Any]:
        old, new = index_by_id(before), index_by_id(after)
        result = self._diff_indexed_maps(old, new, worktable_summary, changed_fields=("powered", "fueled", "operational"))
        bill_changes = []
        for table_id in sorted(old.keys() & new.keys()):
            bills = self._diff_id_list(old[table_id].get("bills"), new[table_id].get("bills"), bill_summary)
            if bills:
                bill_changes.append({"id": table_id, "bills": bills})
        self._bounded_put(result, "billChanges", bill_changes)
        return result

    def _diff_power(self, before: Any, after: Any) -> dict[str, Any]:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return change_value(before, after)
        result: dict[str, Any] = {}
        old_networks = {power_network_key(item): item for item in as_dict_list(before.get("networks"))}
        new_networks = {power_network_key(item): item for item in as_dict_list(after.get("networks"))}
        added = [power_network_summary(new_networks[key]) for key in sorted(new_networks.keys() - old_networks.keys())]
        removed = [power_network_summary(old_networks[key]) for key in sorted(old_networks.keys() - new_networks.keys())]
        changed = []
        for key in sorted(old_networks.keys() & new_networks.keys()):
            delta = power_network_diff(old_networks[key], new_networks[key], self.max_details)
            if delta:
                changed.append({"networkKey": key, **delta})
        self._bounded_put(result, "added", added)
        self._bounded_put(result, "removed", removed)
        self._bounded_put(result, "changed", changed)
        self._put(result, "unpoweredBuildings", self._diff_id_list(before.get("unpoweredBuildings"), after.get("unpoweredBuildings"), power_building_summary))
        return result

    def _diff_fuel(self, before: Any, after: Any) -> dict[str, Any]:
        old, new = index_by_id(before), index_by_id(after)
        result: dict[str, Any] = {}
        self._bounded_put(result, "added", [fuel_summary(new[key]) for key in sorted(new.keys() - old.keys())])
        self._bounded_put(result, "removed", [identity_summary(old[key]) for key in sorted(old.keys() - new.keys())])
        changed = []
        for thing_id in sorted(old.keys() & new.keys()):
            was, now = old[thing_id], new[thing_id]
            delta = fields_diff(was, now, ("targetFuelLevel", "needsFuel"))
            old_band = fuel_band(was)
            new_band = fuel_band(now)
            if old_band != new_band:
                delta["fuel"] = {"from": {"value": was.get("fuel"), "band": old_band}, "to": {"value": now.get("fuel"), "band": new_band}}
            if delta:
                changed.append(with_id(thing_id, delta))
        self._bounded_put(result, "changed", changed)
        return result

    def _diff_generic_collection(self, before: Any, after: Any, nested: bool = False) -> dict[str, Any]:
        if nested and isinstance(before, dict) and isinstance(after, dict):
            result = {}
            for key in sorted(set(before) | set(after)):
                self._put(result, key, self._diff_id_list(before.get(key), after.get(key), generic_entity_summary))
            return result
        return self._diff_id_list(before, after, generic_entity_summary)

    def _diff_id_list(self, before: Any, after: Any, summary: Callable[[Any], Any]) -> dict[str, Any]:
        return self._diff_indexed_maps(index_by_id(before), index_by_id(after), summary)

    def _diff_indexed_maps(
        self,
        old: dict[str, dict[str, Any]],
        new: dict[str, dict[str, Any]],
        summary: Callable[[Any], Any],
        changed_fields: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        self._bounded_put(result, "added", [summary(new[key]) for key in sorted(new.keys() - old.keys())])
        self._bounded_put(result, "removed", [summary(old[key]) for key in sorted(old.keys() - new.keys())])
        changed = []
        for entity_id in sorted(old.keys() & new.keys()):
            delta = fields_diff(old[entity_id], new[entity_id], changed_fields) if changed_fields else generic_entity_diff(old[entity_id], new[entity_id])
            if delta:
                changed.append(with_id(entity_id, delta))
        self._bounded_put(result, "changed", changed)
        return result

    def _bounded_put(self, target: dict[str, Any], key: str, values: list[Any]) -> None:
        if not values:
            return
        target[key] = values[: self.max_details]
        if len(values) > self.max_details:
            target[key + "Count"] = len(values)
            target[key + "DetailsTruncated"] = True
            if self.log:
                self.log(f"[DELTA] {key} details truncated: {len(values)} > {self.max_details}")

    def _bound_final(self, result: dict[str, Any]) -> dict[str, Any]:
        if serialized_chars(result) <= self.max_delta_chars:
            return result
        changes = result.get("changes", {})
        critical = {key: value for key, value in changes.items() if key in {"threats", "colonists"}}
        summarized = {
            key: {"changeCount": count_changes(value), "detailsTruncated": True}
            for key, value in sorted(changes.items())
            if key not in critical
        }
        result["changes"] = {**critical, **summarized}
        result["detailsTruncated"] = True
        if self.log:
            self.log(f"[DELTA] output exceeded {self.max_delta_chars} chars; noncritical sections aggregated")

        if serialized_chars(result) > self.max_delta_chars:
            result["changes"] = {
                **{
                    key: compact_critical_section(key, value, min(10, self.max_details))
                    for key, value in sorted(critical.items())
                },
                **summarized,
            }
            if self.log:
                self.log(f"[DELTA] critical details compacted to enforce the {self.max_delta_chars}-char bound")

        if serialized_chars(result) > self.max_delta_chars:
            result["changes"] = {
                key: {
                    "changeCount": count_changes(value),
                    "sampleIds": collect_ids(value, 3),
                    "detailsTruncated": True,
                }
                for key, value in sorted(changes.items())
            }
            if self.log:
                self.log(f"[DELTA] all sections reduced to counts and sample IDs to enforce the {self.max_delta_chars}-char bound")
        return result

    @staticmethod
    def _put(target: dict[str, Any], key: str, value: Any) -> None:
        if value not in ({}, [], None):
            target[key] = value


def fields_diff(before: Any, after: Any, fields: tuple[str, ...] | None = None) -> dict[str, Any]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return change_value(before, after)
    keys = sorted(fields if fields is not None else (set(before) | set(after)))
    result = {}
    for key in keys:
        if key == "id" or semantically_equal(before.get(key), after.get(key)):
            continue
        result[key] = {"from": compact_value(before.get(key)), "to": compact_value(after.get(key))}
    return result


def scalar_object_diff(before: Any, after: Any) -> dict[str, Any]:
    return fields_diff(before, after)


def generic_entity_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key in sorted(set(before) | set(after)):
        if key in {"id", "growth"} or semantically_equal(before.get(key), after.get(key)):
            continue
        if key == "position":
            change = position_change(before.get(key), after.get(key), 5)
            if change:
                result[key] = change
        elif key == "hitPoints" and not hitpoint_change_meaningful(before, after):
            continue
        else:
            result[key] = {"from": compact_value(before.get(key)), "to": compact_value(after.get(key))}
    return result


def keyed_field_diff(before: Any, after: Any, key_field: str) -> dict[str, Any]:
    old = index_by(before, key_field)
    new = index_by(after, key_field)
    result = {}
    for key in sorted(set(old) | set(new)):
        if key not in old:
            result[key] = {"added": compact_value(new[key])}
        elif key not in new:
            result[key] = {"removed": compact_value(old[key])}
        else:
            delta = fields_diff(old[key], new[key])
            if delta:
                result[key] = delta
    return result


def needs_diff(before: Any, after: Any) -> dict[str, Any]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return change_value(before, after)
    result = {}
    for need in ("food", "mood", "rest", "recreation"):
        old_value, new_value = number(before.get(need)), number(after.get(need))
        old_band, new_band = need_band(need, old_value), need_band(need, new_value)
        if old_band != new_band:
            result[need] = {"from": {"value": old_value, "band": old_band}, "to": {"value": new_value, "band": new_band}}
    return result


def need_band(need: str, value: float | None) -> str:
    if value is None:
        return "unavailable"
    thresholds = {
        "food": ((0.12, "urgent"), (0.30, "low")),
        "mood": ((0.05, "extremeBreakRisk"), (0.18, "majorBreakRisk"), (0.35, "minorBreakRisk")),
        "rest": ((0.14, "exhausted"), (0.30, "tired")),
        "recreation": ((0.15, "deprived"), (0.35, "low")),
    }
    for limit, label in thresholds.get(need, ()):
        if value < limit:
            return label
    return "adequate"


def health_diff(before: Any, after: Any) -> dict[str, Any]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return change_value(before, after)
    result = fields_diff(before, after, ("summary", "downed", "dead"))
    for field, classifier in (("bleedingRate", bleeding_band), ("pain", pain_band)):
        old_value, new_value = number(before.get(field)), number(after.get(field))
        old_band, new_band = classifier(old_value), classifier(new_value)
        if old_band != new_band:
            result[field] = {"from": {"value": old_value, "band": old_band}, "to": {"value": new_value, "band": new_band}}
    hediffs = keyed_severity_diff(before.get("hediffs"), after.get("hediffs"))
    if hediffs:
        result["hediffs"] = hediffs
    return result


def bleeding_band(value: float | None) -> str:
    if not value or value <= 0:
        return "none"
    if value >= 0.5:
        return "critical"
    if value >= 0.15:
        return "serious"
    return "bleeding"


def pain_band(value: float | None) -> str:
    if not value or value < 0.05:
        return "none"
    if value >= 0.6:
        return "severe"
    if value >= 0.3:
        return "moderate"
    return "mild"


def keyed_severity_diff(before: Any, after: Any) -> dict[str, Any]:
    old = aggregate_hediffs(before)
    new = aggregate_hediffs(after)
    result = {}
    for key in sorted(set(old) | set(new)):
        if key not in old:
            result[key] = {"added": new[key]}
        elif key not in new:
            result[key] = {"removed": old[key]}
        elif old[key]["count"] != new[key]["count"] or abs(old[key]["severity"] - new[key]["severity"]) >= 0.1:
            result[key] = {"from": old[key], "to": new[key]}
    return result


def aggregate_hediffs(items: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in as_dict_list(items):
        key = str(item.get("defName") or item.get("label") or "unknown")
        entry = result.setdefault(key, {"count": 0, "severity": 0.0})
        entry["count"] += 1
        entry["severity"] = max(entry["severity"], number(item.get("severity")) or 0.0)
    return result


def environment_diff(before: Any, after: Any) -> dict[str, Any]:
    result = fields_diff(before, after, ("season", "growingSeason", "weather"))
    if isinstance(before, dict) and isinstance(after, dict):
        old_temp, new_temp = number(before.get("outdoorTemperature")), number(after.get("outdoorTemperature"))
        old_band, new_band = temperature_band(old_temp), temperature_band(new_temp)
        if old_band != new_band:
            result["outdoorTemperature"] = {"from": {"value": old_temp, "band": old_band}, "to": {"value": new_temp, "band": new_band}}
    return result


def temperature_band(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < -10:
        return "dangerousCold"
    if value < 0:
        return "freezing"
    if value < 10:
        return "cold"
    if value >= 40:
        return "dangerousHeat"
    if value >= 30:
        return "hot"
    return "mild"


def power_network_diff(before: dict[str, Any], after: dict[str, Any], max_details: int) -> dict[str, Any]:
    result = {}
    old_band, new_band = power_band(before), power_band(after)
    if old_band != new_band:
        result["status"] = {"from": old_band, "to": new_band}
    old_storage, new_storage = storage_band(before), storage_band(after)
    if old_storage != new_storage:
        result["storage"] = {"from": old_storage, "to": new_storage}
    connected = power_building_delta(before.get("connectedBuildings"), after.get("connectedBuildings"), max_details)
    if connected:
        result["connectedBuildings"] = connected
    return result


def power_building_delta(before: Any, after: Any, limit: int) -> dict[str, Any]:
    old, new = index_by_id(before), index_by_id(after)
    result = {}
    added = [power_building_summary(new[key]) for key in sorted(new.keys() - old.keys())]
    removed = [power_building_summary(old[key]) for key in sorted(old.keys() - new.keys())]
    changed = []
    for key in sorted(old.keys() & new.keys()):
        delta = fields_diff(old[key], new[key], ("connected", "powerOn", "switchedOn", "fueled"))
        old_storage, new_storage = storage_band(old[key]), storage_band(new[key])
        if old_storage != new_storage:
            delta["storage"] = {"from": old_storage, "to": new_storage}
        if delta:
            changed.append(with_id(key, delta))
    for name, values in (("added", added), ("removed", removed), ("changed", changed)):
        if values:
            result[name] = values[:limit]
            if len(values) > limit:
                result[name + "Count"] = len(values)
                result[name + "DetailsTruncated"] = True
    return result


def power_band(item: dict[str, Any]) -> str:
    net = number(item.get("netWatts")) or 0.0
    generation = number(item.get("generationWatts")) or 0.0
    consumption = number(item.get("consumptionWatts")) or 0.0
    if generation <= 0 and consumption > 0:
        return "offline"
    if net < -1:
        return "deficit"
    return "stable"


def storage_band(item: dict[str, Any]) -> str:
    stored = number(item.get("storedEnergyWd")) or 0.0
    capacity = number(item.get("batteryCapacityWd")) or 0.0
    if capacity <= 0:
        return "none"
    ratio = stored / capacity
    if ratio <= 0.02:
        return "empty"
    if ratio < 0.20:
        return "low"
    return "adequate"


def fuel_band(item: dict[str, Any]) -> str:
    fuel = number(item.get("fuel")) or 0.0
    capacity = number(item.get("fuelCapacity")) or 0.0
    if fuel <= 0:
        return "empty"
    if capacity > 0 and fuel / capacity < 0.25:
        return "low"
    return "adequate"


def meaningful_progress(before: float | None, after: float | None, cost: float | None) -> bool:
    if before is None or after is None or before == after:
        return False
    if cost and cost > 0:
        if after >= cost > before:
            return True
        return int((before / cost) * 4) != int((after / cost) * 4)
    return abs(after - before) >= 100


def cell_geometry_diff(before: Any, after: Any, limit: int) -> dict[str, Any]:
    old_cells, new_cells = cell_set(before), cell_set(after)
    if old_cells == new_cells:
        return {}
    added = sorted(new_cells - old_cells)
    removed = sorted(old_cells - new_cells)
    result: dict[str, Any] = {}
    if added:
        result["addedCells"] = [{"x": x, "z": z} for x, z in added[:limit]]
        if len(added) > limit:
            result["addedCount"] = len(added)
            result["addedDetailsTruncated"] = True
    if removed:
        result["removedCells"] = [{"x": x, "z": z} for x, z in removed[:limit]]
        if len(removed) > limit:
            result["removedCount"] = len(removed)
            result["removedDetailsTruncated"] = True
    return result


def simple_id_delta(before: Any, after: Any, summary: Callable[[Any], Any], limit: int) -> dict[str, Any]:
    old, new = index_by_id(before), index_by_id(after)
    result = {}
    added = [summary(new[key]) for key in sorted(new.keys() - old.keys())]
    removed = [summary(old[key]) for key in sorted(old.keys() - new.keys())]
    changed = []
    for key in sorted(old.keys() & new.keys()):
        delta = generic_entity_diff(old[key], new[key])
        if delta:
            changed.append(with_id(key, delta))
    for name, values in (("added", added), ("removed", removed), ("changed", changed)):
        if values:
            result[name] = values[:limit]
            if len(values) > limit:
                result[name + "Count"] = len(values)
                result[name + "DetailsTruncated"] = True
    return result


def compact_colonist(item: dict[str, Any]) -> dict[str, Any]:
    result = pick(item, ("id", "name", "kindDef", "position", "drafted", "currentJob", "health", "assignedBed", "allowedArea"))
    if isinstance(item.get("needs"), dict):
        result["needs"] = {key: {"value": value, "band": need_band(key, number(value))} for key, value in sorted(item["needs"].items())}
    result["primaryEquipment"] = equipment_summary(item.get("primaryEquipment"))
    result["apparel"] = [apparel_summary(value) for value in sorted(as_dict_list(item.get("apparel")), key=id_sort_key)[:12]]
    return without_empty(result)


def compact_threat(item: dict[str, Any]) -> dict[str, Any]:
    return without_empty(pick(item, ("id", "type", "defName", "label", "faction", "position", "downed", "weapon")))


def compact_zone(item: dict[str, Any], cell_limit: int) -> dict[str, Any]:
    result = pick(item, ("id", "type", "label", "cellCount", "bounds", "plantDef", "priority", "preset"))
    cells = sorted(cell_set(item.get("cells")))
    if cells:
        result["cells"] = [{"x": x, "z": z} for x, z in cells[:cell_limit]]
        if len(cells) > cell_limit:
            result["cellDetailsTruncated"] = True
    return without_empty(result)


def construction_key(item: dict[str, Any]) -> tuple[Any, ...]:
    def_name = str(item.get("defName") or "")
    for prefix in ("Blueprint_", "Frame_"):
        if def_name.startswith(prefix):
            def_name = def_name[len(prefix):]
    position = item.get("position") if isinstance(item.get("position"), dict) else {}
    return (def_name, position.get("x"), position.get("z"), item.get("rotation"))


def construction_rank(item: dict[str, Any]) -> int:
    return {"blueprint": 1, "frame": 2, "building": 3}.get(str(item.get("type")), 0)


def construction_summary(item: Any) -> Any:
    return compact_summary(item, ("id", "type", "defName", "label", "position", "rotation", "size", "hitPoints", "maxHitPoints", "powered"))


def equipment_summary(item: Any) -> Any:
    return compact_summary(item, ("id", "defName", "label", "weaponType", "quality", "hitPoints", "maxHitPoints", "forbidden", "reserved", "position"))


def apparel_summary(item: Any) -> Any:
    return compact_summary(item, ("id", "defName", "label", "quality", "hitPoints", "maxHitPoints", "wornBy", "tainted", "forbidden", "position"))


def bed_summary(item: Any) -> Any:
    value = compact_summary(item, ("id", "defName", "label", "position", "medical", "forPrisoners", "owners"))
    if isinstance(value, dict) and isinstance(value.get("owners"), list):
        value["owners"] = sorted(value["owners"], key=str)
    return value


def worktable_summary(item: Any) -> Any:
    return compact_summary(item, ("id", "defName", "label", "position", "powered", "fueled", "operational"))


def bill_summary(item: Any) -> Any:
    return compact_summary(item, ("id", "recipeDef", "label", "repeatMode", "targetCount", "repeatCount", "suspended", "ingredientSearchRadius", "pawnRestriction"))


def power_building_summary(item: Any) -> Any:
    return compact_summary(item, ("id", "defName", "label", "powerType", "position", "connected", "powerOn", "switchedOn", "fueled"))


def power_network_summary(item: Any) -> Any:
    if not isinstance(item, dict):
        return compact_value(item)
    return {"id": item.get("id"), "networkKey": power_network_key(item), "status": power_band(item), "storage": storage_band(item)}


def fuel_summary(item: Any) -> Any:
    if not isinstance(item, dict):
        return compact_value(item)
    result = compact_summary(item, ("id", "defName", "label", "position", "fuel", "fuelCapacity", "targetFuelLevel", "needsFuel"))
    result["band"] = fuel_band(item)
    return result


def plant_summary(item: Any) -> Any:
    return compact_summary(item, ("id", "defName", "label", "position", "mature", "harvestableNow", "dead"))


def research_summary(item: Any) -> Any:
    return compact_summary(item, ("defName", "label", "progress", "cost"))


def generic_entity_summary(item: Any) -> Any:
    return compact_summary(item, ("id", "type", "defName", "label", "stackCount", "position", "forbidden"))


def identity_summary(item: Any) -> Any:
    return compact_summary(item, ("id", "name", "type", "defName", "label"))


def compact_summary(item: Any, fields: tuple[str, ...]) -> Any:
    if not isinstance(item, dict):
        return compact_value(item)
    return without_empty(pick(item, fields))


def compact_value(value: Any, list_limit: int = 20) -> Any:
    if isinstance(value, dict):
        return {str(key): compact_value(value[key], list_limit) for key in sorted(value)}
    if isinstance(value, list):
        canonical = sorted((compact_value(item, list_limit) for item in value), key=canonical_json)
        if len(canonical) > list_limit:
            return {"items": canonical[:list_limit], "totalCount": len(canonical), "detailsTruncated": True}
        return canonical
    return value


def semantically_equal(before: Any, after: Any) -> bool:
    return canonical_json(compact_value(before, 1000)) == canonical_json(compact_value(after, 1000))


def index_by_id(items: Any) -> dict[str, dict[str, Any]]:
    return index_by(items, "id")


def index_by(items: Any, key_field: str) -> dict[str, dict[str, Any]]:
    result = {}
    for item in as_dict_list(items):
        key = item.get(key_field)
        if key is not None:
            result[str(key)] = item
    return result


def as_dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def cell_set(value: Any) -> set[tuple[int, int]]:
    result = set()
    for item in as_dict_list(value):
        x, z = item.get("x"), item.get("z")
        if isinstance(x, int) and isinstance(z, int):
            result.add((x, z))
    return result


def position_change(before: Any, after: Any, minimum_distance: int) -> dict[str, Any]:
    if semantically_equal(before, after):
        return {}
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"from": compact_value(before), "to": compact_value(after)}
    distance = abs(int(after.get("x", 0)) - int(before.get("x", 0))) + abs(int(after.get("z", 0)) - int(before.get("z", 0)))
    return {"from": compact_value(before), "to": compact_value(after)} if distance >= minimum_distance else {}


def hitpoint_change_meaningful(before: dict[str, Any], after: dict[str, Any]) -> bool:
    old, new = number(before.get("hitPoints")), number(after.get("hitPoints"))
    maximum = number(after.get("maxHitPoints")) or number(before.get("maxHitPoints"))
    if old is None or new is None or maximum is None or maximum <= 0:
        return old != new
    return abs(new - old) / maximum >= 0.1 or (old > 0 and new <= 0)


def power_network_key(item: dict[str, Any]) -> str:
    members = sorted(str(value.get("id")) for value in as_dict_list(item.get("connectedBuildings")) if value.get("id"))
    if members:
        digest = hashlib.sha256("\0".join(members).encode("utf-8")).hexdigest()[:12]
        return "members-" + digest
    return str(item.get("id") or "unknown")


def set_nested(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = target
    for key in path[:-1]:
        cursor = cursor.setdefault(key, {})
    if path:
        cursor[path[-1]] = value


def pick(item: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: copy.deepcopy(item.get(field)) for field in fields if field in item}


def without_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, [], {})}


def with_id(entity_id: str, delta: dict[str, Any]) -> dict[str, Any]:
    return {"id": entity_id, **delta}


def change_value(before: Any, after: Any) -> dict[str, Any]:
    if semantically_equal(before, after):
        return {}
    return {"value": {"from": compact_value(before), "to": compact_value(after)}}


def object_value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def serialized_chars(value: Any) -> int:
    return len(canonical_json(value))


def count_changes(value: Any) -> int:
    if isinstance(value, list):
        return sum(count_changes(item) for item in value) or len(value)
    if isinstance(value, dict):
        return sum(count_changes(item) for item in value.values()) or 1
    return 1


def compact_critical_section(section: str, value: Any, limit: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"changeCount": count_changes(value), "detailsTruncated": True}
    result: dict[str, Any] = {}
    for key in sorted(value):
        item = value[key]
        if isinstance(item, list):
            result[key] = [compact_critical_item(section, entry) for entry in item[:limit]]
            result[key + "Count"] = int(value.get(key + "Count", len(item)))
            if len(item) > limit or value.get(key + "DetailsTruncated"):
                result[key + "DetailsTruncated"] = True
        elif key.endswith("Count") or key.endswith("DetailsTruncated"):
            result[key] = item
    result["detailsTruncated"] = True
    return result


def compact_critical_item(section: str, value: Any) -> Any:
    if not isinstance(value, dict):
        return compact_value(value, 4)
    if section == "threats":
        summary = pick(value, ("id", "type", "defName", "label", "downed"))
        for field in ("weapon", "position"):
            if field in value:
                summary[field] = compact_value(value[field], 4)
        return without_empty(summary)
    summary = pick(value, ("id", "name", "drafted", "mentalState", "assignedBed", "allowedArea"))
    if "health" in value:
        summary["health"] = compact_value(value["health"], 4)
    if "primaryEquipment" in value:
        summary["primaryEquipment"] = compact_value(value["primaryEquipment"], 4)
    return without_empty(summary)


def collect_ids(value: Any, limit: int) -> list[str]:
    ids: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            entity_id = item.get("id")
            if entity_id is not None:
                ids.add(str(entity_id))
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(ids)[:limit]


def id_sort_key(item: dict[str, Any]) -> str:
    return str(item.get("id") or "")
