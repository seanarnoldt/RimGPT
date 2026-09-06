import copy
import json
import unittest

from agent_controller import SYSTEM_INSTRUCTIONS
from context_telemetry import measure_context, serialized_chars
from model_tool_result import (
    ModelToolResultFormatter,
    compact_inspect_map,
    decode_inspect_map,
)
from tools import TOOLS


def command_result(data=None, *, success=True, error=None):
    result = {
        "commandId": "command-1234567890",
        "status": "completed",
        "success": success,
        "message": "Verbose command completion message that the model does not need",
        "elapsedSeconds": 1.234,
        "command": {"command": "echo", "payload": "x" * 500},
        "submittedAt": "2026-01-01T00:00:00Z",
        "completedAt": "2026-01-01T00:00:01Z",
    }
    if data is not None:
        result["data"] = data
    if error is not None:
        result["error"] = error
    return result


def placement(index, valid=True, reason=None, affordance="Medium"):
    return {"index": index, "valid": valid, "reason": reason, "requiredTerrainAffordance": affordance}


def raw_map(size, *, obstacle=False, varied=False):
    base_cell = {
        "terrain": "Soil",
        "terrainLabel": "soil",
        "fertility": 1.0,
        "walkable": True,
        "buildable": True,
        "roofed": False,
        "water": False,
        "canCreateGrowingZone": True,
        "canCreateStockpile": True,
        "affordances": ["Light", "Medium", "Heavy"],
    }
    rock_cell = {
        "terrain": "Gravel",
        "terrainLabel": "gravel",
        "fertility": 0.0,
        "walkable": True,
        "buildable": False,
        "roofed": True,
        "water": False,
        "canCreateGrowingZone": False,
        "canCreateStockpile": False,
        "affordances": ["Light", "Medium", "Heavy"],
    }
    rows = []
    for z in range(100, 100 + size):
        if varied and z == 101:
            rows.append({"z": z, "runs": [{"x": 100, "len": 2, "cell": base_cell}, {"x": 102, "len": 1, "cell": rock_cell}, {"x": 103, "len": size - 3, "cell": base_cell}]})
        else:
            rows.append({"z": z, "runs": [{"x": 100, "len": size, "cell": base_cell}]})
    things = []
    if obstacle:
        things.append({"id": "Building_Rock1", "type": "building", "defName": "Wall", "label": "granite wall", "position": {"x": 102, "z": 101}, "rotation": "North", "size": {"x": 1, "z": 1}, "stackCount": 1, "hitPoints": 300, "maxHitPoints": 300, "powered": None, "forbidden": False})
        things.append({"id": "Plant_1", "type": "plant", "defName": "Plant_TreeOak", "label": "oak tree", "position": {"x": 103, "z": 103}, "growth": 0.4321, "mature": True, "harvestableNow": False, "canDesignateCut": True, "canDesignateHarvest": False, "forbidden": False})
    return {"schemaVersion": 2, "gameLoaded": True, "mapId": "map-7", "bounds": {"minX": 100, "minZ": 100, "maxX": 99 + size, "maxZ": 99 + size}, "terrainRows": rows, "things": things, "zones": []}


def room_aware_map(size=20, *, enclosed=True):
    result = raw_map(size, obstacle=True, varied=True)
    room = {
        "id": "room-7-42",
        "indoors": enclosed,
        "enclosed": enclosed,
        "usesOutdoorTemperature": not enclosed,
        "suitableForTemperatureControl": enclosed,
        "cellCount": size * size,
        "roofedCellCount": size * size if enclosed else 0,
        "roofCoverage": 1.0 if enclosed else 0.0,
        "temperature": 21.5 if enclosed else 42.0,
        "bounds": copy.deepcopy(result["bounds"]),
    }
    for row in result["terrainRows"]:
        for run in row["runs"]:
            run["cell"] = {**run["cell"], "room": copy.deepcopy(room)}
    result["things"][0]["room"] = copy.deepcopy(room)
    return result


class ToolResultCompressionTests(unittest.TestCase):
    def setUp(self):
        self.formatter = ModelToolResultFormatter(logger=lambda _: None)

    def test_simple_command_success_is_compact_and_removes_echo(self):
        raw = command_result({"requestedSpeed": 2})
        model = self.formatter.format("set_speed", raw, {"speed": 2})
        self.assertEqual(model, {"success": True, "speed": 2})
        self.assertLess(serialized_chars(model), 100)
        self.assertNotIn("commandId", model)
        self.assertNotIn("command", model)

    def test_simple_failure_retains_reason_and_relevant_identity(self):
        raw = command_result(success=False, error="Pawn cannot perform Doctor work")
        model = self.formatter.format("set_work_priority", raw, {"pawn_id": "Human835", "work_type": "Doctor", "priority": 1})
        self.assertFalse(model["success"])
        self.assertEqual(model["pawnId"], "Human835")
        self.assertEqual(model["workType"], "Doctor")
        self.assertIn("cannot perform", model["reason"])

    def test_formatter_does_not_mutate_raw_result(self):
        raw = command_result({"requestedSpeed": 1})
        before = copy.deepcopy(raw)
        self.formatter.format("set_speed", raw, {"speed": 1})
        self.assertEqual(raw, before)

    def test_completed_command_semantics_cover_operational_actions(self):
        cases = (
            ("equip_weapon", {"pawn_id": "P1", "thing_id": "Gun1"}, {"primaryEquipmentId": "Gun1"}),
            ("wear_apparel", {"pawn_id": "P1", "thing_id": "Hat1"}, {"apparelId": "Hat1"}),
            ("assign_bed", {"pawn_id": "P1", "bed_id": "Bed1"}, {"bedId": "Bed1"}),
            ("set_power_switch", {"thing_id": "Switch1", "on": True}, {"on": True}),
            ("set_target_fuel_level", {"thing_id": "Gen1", "level": 0.75}, {"targetFuelLevel": 0.75}),
            ("prioritize_haul", {"pawn_id": "P1", "thing_id": "Steel1"}, {"targetId": "Steel1"}),
        )
        for name, arguments, expected in cases:
            with self.subTest(name=name):
                model = self.formatter.format(name, command_result(), arguments)
                self.assertTrue(model["success"])
                for key, value in expected.items():
                    self.assertEqual(model[key], value)
                self.assertLess(serialized_chars(model), 500)

    def test_bill_results_contain_only_changed_settings(self):
        added = self.formatter.format("add_bill", command_result({"billId": "Bill_1"}), {"worktable_id": "Bench1", "recipe_def": "MakeMealSimple", "repeat_mode": "untilX", "target_count": 20})
        modified = self.formatter.format("set_bill_target_count", command_result(), {"worktable_id": "Bench1", "bill_id": "Bill_1", "target_count": 30})
        suspended = self.formatter.format("set_bill_suspended", command_result(), {"worktable_id": "Bench1", "bill_id": "Bill_1", "suspended": True})
        self.assertEqual(added["billId"], "Bill_1")
        self.assertEqual(added["targetCount"], 20)
        self.assertEqual(modified["targetCount"], 30)
        self.assertEqual(suspended["suspended"], True)

    def test_blueprint_batch_full_success_has_no_success_entries(self):
        arguments = {"placements": [{"build_def": "Wall", "x": i, "z": 5} for i in range(25)]}
        raw = command_result({"placements": [{"index": i, "success": True, "message": "Blueprint placed", "blueprintId": f"B{i}"} for i in range(25)]})
        model = self.formatter.format("place_blueprints", raw, arguments)
        self.assertEqual(model, {"success": True, "requested": 25, "placed": 25, "failed": []})
        self.assertLess(serialized_chars(model), 500)

    def test_blueprint_batch_partial_failure_retains_exact_exceptions(self):
        arguments = {"placements": [{"build_def": "Wall", "x": 100 + i, "z": 93} for i in range(25)]}
        outcomes = [{"index": i, "success": i not in (7, 19), "message": "blocked" if i == 7 else ("insufficient terrain support" if i == 19 else "placed")} for i in range(25)]
        model = self.formatter.format("place_blueprints", command_result({"placements": outcomes}), arguments)
        self.assertFalse(model["success"])
        self.assertEqual(model["placed"], 23)
        self.assertEqual([(item["index"], item["x"]) for item in model["failed"]], [(7, 107), (19, 119)])

    def test_zone_creation_reports_counts_without_cells(self):
        arguments = {"min_x": 10, "min_z": 20, "max_x": 19, "max_z": 25}
        raw = command_result({"zoneId": "Zone_18", "cellsAdded": 60})
        model = self.formatter.format("create_stockpile", raw, arguments)
        self.assertEqual(model["zoneId"], "Zone_18")
        self.assertEqual(model["createdCells"], 60)
        self.assertNotIn("cells", model)

    def test_build_validator_full_success_summarizes(self):
        arguments = {"placements": [{"build_def": "Wall", "x": i, "z": 1} for i in range(25)]}
        raw = {"placements": [placement(i) for i in range(25)]}
        model = self.formatter.format("check_build_placements", {"success": True, "result": raw}, arguments)
        self.assertEqual(model, {"success": True, "valid": True, "requested": 25, "validCount": 25, "invalid": []})

    def test_build_validator_failure_retains_cell_reason_and_affordance(self):
        arguments = {"placements": [{"build_def": "Wall", "x": 10 + i, "z": 20} for i in range(3)]}
        raw = {"placements": [placement(0), placement(1, False, "Wall requires terrain that supports Medium"), placement(2)]}
        model = self.formatter.format("check_build_placements", {"success": True, "result": raw}, arguments)
        self.assertFalse(model["valid"])
        self.assertEqual(model["invalid"][0]["x"], 11)
        self.assertEqual(model["invalid"][0]["requiredTerrainAffordance"], "Medium")

    def test_zone_validator_full_and_partial_results(self):
        full = {"zoneType": "growing", "requestedBounds": {"minX": 1, "minZ": 1, "maxX": 5, "maxZ": 5}, "requestedCells": 25, "validCells": 25, "invalidCells": 0, "cellLimit": 400, "cells": [{"x": x, "z": z, "valid": True, "reason": None} for x in range(1, 6) for z in range(1, 6)], "invalidReasons": []}
        model = self.formatter.format("check_zone_placement", {"success": True, "result": full}, {})
        self.assertTrue(model["valid"])
        self.assertEqual(model["invalid"], [])
        partial = copy.deepcopy(full)
        partial["validCells"] = 24
        partial["invalidCells"] = 1
        partial["cells"][0] = {"x": 1, "z": 1, "valid": False, "reason": "Blocked by building"}
        partial["invalidReasons"] = [{"reason": "Blocked by building", "count": 1}]
        model = self.formatter.format("check_zone_placement", {"success": True, "result": partial}, {})
        self.assertFalse(model["valid"])
        self.assertEqual(model["invalid"], [{"x": 1, "z": 1, "reason": "Blocked by building"}])

    def test_inspect_map_round_trip_preserves_planning_cell_facts(self):
        raw = raw_map(10, obstacle=True, varied=True)
        compact = compact_inspect_map(raw)
        decoded = decode_inspect_map(compact)
        self.assertEqual(decoded[(100, 100)]["terrain"], "Soil")
        self.assertEqual(decoded[(102, 101)]["buildable"], False)
        self.assertEqual(decoded[(102, 101)]["affordances"], ["Light", "Medium", "Heavy"])
        self.assertEqual(decoded[(102, 101)]["growingZone"], False)
        self.assertEqual(compact["things"][0]["id"], "Building_Rock1")
        self.assertEqual(compact["things"][0]["x"], 102)
        self.assertEqual(compact["plantGroups"][0]["cells"], [[103, 103]])

    def test_inspect_map_is_deterministic_and_smaller_at_common_sizes(self):
        for size in (10, 20, 30):
            with self.subTest(size=size):
                raw = raw_map(size, obstacle=True, varied=True)
                first = self.formatter.format("inspect_map", {"success": True, "result": raw}, {})
                second = self.formatter.format("inspect_map", {"success": True, "result": raw}, {})
                self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
                self.assertLess(serialized_chars(first), serialized_chars(raw) * 0.65)

    def test_room_aware_20_by_20_uses_shared_room_table_with_bounded_output(self):
        compact = self.formatter.format("inspect_map", {"success": True, "result": room_aware_map()}, {})
        decoded = decode_inspect_map(compact)
        room_refs = {
            cell["room"]
            for item in compact["terrainPalette"]
            if (cell := {key: value for key, value in item.items() if key != "id"}).get("room", "").startswith("r")
        }
        self.assertEqual(len(compact["rooms"]), 1)
        self.assertEqual(room_refs, {"r0"})
        self.assertTrue(decoded[(100, 100)]["room"]["suitableForTemperatureControl"])
        self.assertEqual(decoded[(100, 100)]["room"]["cellCount"], 400)
        self.assertLess(serialized_chars(compact), 8_000)

    def test_outdoor_room_uses_sentinel_and_retains_temperature_safety_facts(self):
        compact = self.formatter.format("inspect_map", {"success": True, "result": room_aware_map(enclosed=False)}, {})
        decoded = decode_inspect_map(compact)
        self.assertEqual(compact["terrainPalette"][0]["room"], "outdoor")
        self.assertEqual(compact["rooms"], [])
        room = decoded[(100, 100)]["room"]
        self.assertFalse(room["enclosed"])
        self.assertFalse(room["suitableForTemperatureControl"])
        self.assertEqual(room["temperature"], 42.0)

    def test_adjacent_room_reference_is_promoted_when_full_room_facts_arrive(self):
        raw = room_aware_map()
        first_cell = raw["terrainRows"][0]["runs"][0]["cell"]
        raw["terrainRows"][0]["runs"][0]["cell"] = {
            **{key: value for key, value in first_cell.items() if key != "room"},
            "room": None,
            "adjacentRoomIds": ["room-7-42"],
        }
        compact = self.formatter.format("inspect_map", {"success": True, "result": raw}, {})
        self.assertEqual(compact["terrainPalette"][0]["adjacentRooms"], ["r0"])
        self.assertTrue(compact["rooms"][0]["indoors"])
        self.assertTrue(compact["rooms"][0]["suitableForTemperatureControl"])

    def test_map_result_truncation_preserves_critical_thing(self):
        raw = raw_map(30)
        raw["things"] = [
            {"id": "Hostile_1", "type": "hostilePawn", "defName": "Human", "label": "raider", "position": {"x": 101, "z": 101}, "rotation": "North"}
        ] + [
            {"id": f"Item_{i}", "type": "item", "defName": f"Resource_{i}", "label": "x" * 300, "position": {"x": 102, "z": 102}, "rotation": "North"}
            for i in range(239)
        ]
        model = self.formatter.format("inspect_map", {"success": True, "result": raw}, {})
        self.assertTrue(model["truncated"])
        self.assertLessEqual(serialized_chars(model), 8_000)
        self.assertEqual(model["things"][0]["id"], "Hostile_1")

    def test_catalogs_are_bounded_and_retain_command_defs(self):
        build_raw = {"options": [{"defName": f"Building_{i}", "label": "x" * 200, "size": {"x": 1, "z": 1}, "stuffable": True, "requiredTerrainAffordance": "Medium", "cost": [{"defName": "WoodLog", "count": 5}]} for i in range(100)]}
        plants_raw = {"plants": [{"defName": f"Plant_{i}", "label": "x" * 200, "fertilityMin": 0.7, "sowMinSkill": 2, "growthSeasonNow": True} for i in range(150)]}
        recipes_raw = {"worktableId": "Bench1", "recipes": [{"recipeDef": f"Recipe_{i}", "label": "x" * 200, "currentlyAvailable": True, "ingredients": [{"summary": "steel", "count": 10}], "products": [{"defName": "Product", "count": 1}]} for i in range(200)]}
        for name, raw, field in (("list_build_options", build_raw, "options"), ("list_growable_plants", plants_raw, "plants"), ("list_recipes", recipes_raw, "recipes")):
            with self.subTest(name=name):
                model = self.formatter.format(name, {"success": True, "result": raw}, {})
                self.assertLessEqual(serialized_chars(model), 12_000)
                self.assertTrue(model["truncated"])
                self.assertTrue(model[field])

    def test_get_colony_state_removes_wrapper_but_preserves_section(self):
        section = {"section": "resources", "snapshotVersion": 8, "truncated": False, "data": {"available": {"wood": 50}}}
        model = self.formatter.format("get_colony_state", {"success": True, "result": section, "elapsedSeconds": 0.5}, {"section": "resources"})
        self.assertEqual(model["section"], "resources")
        self.assertNotIn("result", model)
        self.assertNotIn("elapsedSeconds", model)

    def test_tool_result_telemetry_reports_actual_sizes(self):
        raw = command_result({"requestedSpeed": 2})
        model, telemetry = self.formatter.format_with_telemetry("set_speed", raw, {"speed": 2})
        self.assertEqual(telemetry.raw_chars, serialized_chars(raw))
        self.assertEqual(telemetry.model_chars, serialized_chars(model))
        self.assertAlmostEqual(telemetry.compression_ratio, telemetry.raw_chars / telemetry.model_chars)
        self.assertTrue(telemetry.success)

    def test_formatter_failure_is_conservative_and_bounded(self):
        class BrokenFormatter(ModelToolResultFormatter):
            def _format(self, tool_name, raw, arguments):
                raise RuntimeError("formatter exploded " + "x" * 5000)

        model = BrokenFormatter(logger=lambda _: None).format("set_speed", command_result(), {"speed": 2})
        self.assertFalse(model["success"])
        self.assertTrue(model["truncated"])
        self.assertLess(serialized_chars(model), 2000)

    def test_mocked_tool_heavy_cycle_stays_below_context_guard(self):
        results = [
            self.formatter.format("inspect_map", {"success": True, "result": raw_map(30, obstacle=True, varied=True)}, {}),
            self.formatter.format("check_build_placements", {"success": True, "result": {"placements": [placement(i) for i in range(25)]}}, {"placements": [{"build_def": "Wall", "x": 100 + i, "z": 100} for i in range(25)]}),
            self.formatter.format("place_blueprints", command_result({"placements": [{"index": i, "success": True, "message": "placed"} for i in range(25)]}), {"placements": [{"build_def": "Wall", "x": 100 + i, "z": 100} for i in range(25)]}),
            {"success": True, "section": "buildings", "snapshotVersion": 20, "truncated": False, "data": {"countsByDef": [{"defName": "Wall", "count": 25}], "entries": [{"id": f"Wall_{i}", "defName": "Wall", "position": {"x": 100 + i, "z": 100}} for i in range(25)]}},
        ]
        carried = 1500
        estimates = []
        accumulated = 0
        for result in results:
            output = {"type": "function_call_output", "call_id": "call", "output": json.dumps(result, separators=(",", ":"))}
            chars = serialized_chars(result)
            accumulated += chars
            breakdown = measure_context(
                instructions=SYSTEM_INSTRUCTIONS,
                tools=TOOLS,
                input_items=[output],
                state={"operations": {}, "colonists": [], "map": {}},
                accumulated_tool_result_chars=accumulated,
                carried_context_chars=carried,
                tool_result_chars_this_round=chars,
            )
            estimates.append(breakdown.estimated_input_tokens)
            carried += serialized_chars(output) + 300
        self.assertLess(max(estimates), 30_000)

    def test_repeated_room_aware_map_results_stay_below_context_guard(self):
        results = [
            self.formatter.format("inspect_map", {"success": True, "result": room_aware_map()}, {})
            for _ in range(3)
        ]
        carried = 1_500
        estimates = []
        accumulated = 0
        for index, result in enumerate(results):
            output = {"type": "function_call_output", "call_id": f"map-{index}", "output": json.dumps(result, separators=(",", ":"))}
            chars = serialized_chars(result)
            accumulated += chars
            breakdown = measure_context(
                instructions=SYSTEM_INSTRUCTIONS,
                tools=TOOLS,
                input_items=[output],
                state={"operations": {}, "colonists": [], "map": {}},
                accumulated_tool_result_chars=accumulated,
                carried_context_chars=carried,
                tool_result_chars_this_round=chars,
            )
            estimates.append(breakdown.estimated_input_tokens)
            carried += serialized_chars(output) + 300
        self.assertLess(max(estimates), 30_000)


if __name__ == "__main__":
    unittest.main()
