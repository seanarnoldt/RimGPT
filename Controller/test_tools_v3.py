import unittest

from model_tool_result import ModelToolResultFormatter
from tool_registry import DEFAULT_TOOL_REGISTRY
from tools import TOOLS, is_read_only_tool, tool_call_to_bridge_command


V3_TOOL_GROUPS = {
    "equipment": {"equip_weapon", "drop_primary_weapon", "wear_apparel", "remove_apparel", "assign_bed", "unassign_bed"},
    "production": {"list_recipes", "add_bill", "set_bill_suspended", "remove_bill", "set_bill_target_count"},
    "power": {"set_power_switch", "set_target_fuel_level"},
    "zones": {"create_allowed_area", "set_allowed_area_cells", "assign_allowed_area"},
    "work": {"prioritize_haul", "prioritize_rescue", "prioritize_tend", "prioritize_clean", "prioritize_refuel", "prioritize_construct"},
}

V3_COMMAND_CASES = (
    ("equip_weapon", {"pawn_id": "Pawn_1", "thing_id": "Gun_1"}, {"command": "equipWeapon", "pawnId": "Pawn_1", "thingId": "Gun_1"}),
    ("drop_primary_weapon", {"pawn_id": "Pawn_1"}, {"command": "dropPrimaryWeapon", "pawnId": "Pawn_1"}),
    ("wear_apparel", {"pawn_id": "Pawn_1", "thing_id": "Hat_1"}, {"command": "wearApparel", "pawnId": "Pawn_1", "thingId": "Hat_1"}),
    ("remove_apparel", {"pawn_id": "Pawn_1", "thing_id": "Hat_1"}, {"command": "removeApparel", "pawnId": "Pawn_1", "thingId": "Hat_1"}),
    ("assign_bed", {"pawn_id": "Pawn_1", "bed_id": "Bed_1"}, {"command": "assignBed", "pawnId": "Pawn_1", "bedId": "Bed_1"}),
    ("unassign_bed", {"pawn_id": "Pawn_1"}, {"command": "unassignBed", "pawnId": "Pawn_1"}),
    ("add_bill", {"worktable_id": "Bench_1", "recipe_def": "MakeMealSimple", "repeat_mode": "untilX", "target_count": 10}, {"command": "addBill", "worktableId": "Bench_1", "recipeDef": "MakeMealSimple", "repeatMode": "untilX", "targetCount": 10}),
    ("set_bill_suspended", {"worktable_id": "Bench_1", "bill_id": "Bill_1", "suspended": True}, {"command": "setBillSuspended", "worktableId": "Bench_1", "billId": "Bill_1", "suspended": True}),
    ("remove_bill", {"worktable_id": "Bench_1", "bill_id": "Bill_1"}, {"command": "removeBill", "worktableId": "Bench_1", "billId": "Bill_1"}),
    ("set_bill_target_count", {"worktable_id": "Bench_1", "bill_id": "Bill_1", "target_count": 12}, {"command": "setBillTargetCount", "worktableId": "Bench_1", "billId": "Bill_1", "targetCount": 12}),
    ("set_power_switch", {"thing_id": "Switch_1", "on": False}, {"command": "setPowerSwitch", "thingId": "Switch_1", "on": False}),
    ("set_target_fuel_level", {"thing_id": "Generator_1", "level": 25.5}, {"command": "setTargetFuelLevel", "thingId": "Generator_1", "level": 25.5}),
    ("create_allowed_area", {"label": "Indoors"}, {"command": "createAllowedArea", "label": "Indoors"}),
    ("set_allowed_area_cells", {"area_id": "Area_1", "cells": [{"x": 10, "z": 20}], "allowed": True}, {"command": "setAllowedAreaCells", "areaId": "Area_1", "cells": [{"x": 10, "z": 20}], "allowed": True}),
    ("assign_allowed_area", {"pawn_id": "Pawn_1", "area_id": None}, {"command": "assignAllowedArea", "pawnId": "Pawn_1", "areaId": None}),
    ("prioritize_haul", {"pawn_id": "Pawn_1", "thing_id": "Steel_1"}, {"command": "prioritizeHaul", "pawnId": "Pawn_1", "thingId": "Steel_1"}),
    ("prioritize_rescue", {"pawn_id": "Pawn_1", "target_pawn_id": "Pawn_2"}, {"command": "prioritizeRescue", "pawnId": "Pawn_1", "targetPawnId": "Pawn_2"}),
    ("prioritize_tend", {"pawn_id": "Pawn_1", "target_pawn_id": "Pawn_2"}, {"command": "prioritizeTend", "pawnId": "Pawn_1", "targetPawnId": "Pawn_2"}),
    ("prioritize_clean", {"pawn_id": "Pawn_1", "x": 10, "z": 20}, {"command": "prioritizeClean", "pawnId": "Pawn_1", "x": 10, "z": 20}),
    ("prioritize_refuel", {"pawn_id": "Pawn_1", "thing_id": "Generator_1"}, {"command": "prioritizeRefuel", "pawnId": "Pawn_1", "thingId": "Generator_1"}),
    ("prioritize_construct", {"pawn_id": "Pawn_1", "blueprint_or_frame_id": "Blueprint_1"}, {"command": "prioritizeConstruct", "pawnId": "Pawn_1", "blueprintOrFrameId": "Blueprint_1"}),
)


class ToolV3Tests(unittest.TestCase):
    def test_v3_tools_have_exactly_one_registry_group_and_executor(self):
        schema_names = {tool["name"] for tool in TOOLS}
        expected_names = set().union(*V3_TOOL_GROUPS.values())
        self.assertTrue(expected_names.issubset(schema_names))
        for group, names in V3_TOOL_GROUPS.items():
            for name in names:
                with self.subTest(group=group, tool=name):
                    registration = DEFAULT_TOOL_REGISTRY.registration(name)
                    self.assertIsNotNone(registration)
                    self.assertEqual(registration.group, group)
                    self.assertTrue(registration.executor)

    def test_list_recipes_is_the_only_v3_read_tool(self):
        self.assertTrue(is_read_only_tool("list_recipes"))
        for names in V3_TOOL_GROUPS.values():
            for name in names - {"list_recipes"}:
                with self.subTest(tool=name):
                    self.assertFalse(is_read_only_tool(name))

    def test_every_v3_command_translates_to_the_bridge_protocol(self):
        for name, arguments, expected in V3_COMMAND_CASES:
            with self.subTest(tool=name):
                self.assertEqual(tool_call_to_bridge_command(name, arguments), expected)

    def test_add_bill_omits_null_target_count(self):
        self.assertEqual(
            tool_call_to_bridge_command("add_bill", {"worktable_id": "Bench_1", "recipe_def": "MakeMealSimple", "repeat_mode": "forever", "target_count": None}),
            {"command": "addBill", "worktableId": "Bench_1", "recipeDef": "MakeMealSimple", "repeatMode": "forever"},
        )

    def test_v3_success_and_failure_results_are_compact(self):
        formatter = ModelToolResultFormatter(logger=lambda _: None)
        raw_success = {"commandId": "cmd-1", "status": "completed", "success": True, "message": "completed", "command": {"large": "x" * 1000}}
        for name, arguments, _ in V3_COMMAND_CASES:
            with self.subTest(tool=name):
                result = formatter.format(name, raw_success, arguments)
                self.assertTrue(result["success"])
                self.assertLess(len(str(result)), 500)
                self.assertNotIn("commandId", result)
                self.assertNotIn("command", result)

        failure = formatter.format("equip_weapon", {"status": "completed", "success": False, "error": "Weapon is reserved"}, {"pawn_id": "Pawn_1", "thing_id": "Gun_1"})
        self.assertEqual(failure, {"success": False, "reason": "Weapon is reserved", "pawnId": "Pawn_1", "thingId": "Gun_1"})


if __name__ == "__main__":
    unittest.main()
