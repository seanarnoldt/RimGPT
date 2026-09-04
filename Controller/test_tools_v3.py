import unittest

from tools import TOOLS, is_read_only_tool, tool_call_to_bridge_command


class ToolV3Tests(unittest.TestCase):
    def test_list_recipes_is_read_only(self):
        names = {tool["name"] for tool in TOOLS}
        self.assertIn("list_recipes", names)
        self.assertTrue(is_read_only_tool("list_recipes"))

    def test_operational_command_mappings(self):
        self.assertEqual(
            tool_call_to_bridge_command("equip_weapon", {"pawn_id": "Pawn_1", "thing_id": "Gun_1"}),
            {"command": "equipWeapon", "pawnId": "Pawn_1", "thingId": "Gun_1"},
        )
        self.assertEqual(
            tool_call_to_bridge_command(
                "add_bill",
                {"worktable_id": "Bench_1", "recipe_def": "MakeMealSimple", "repeat_mode": "untilX", "target_count": 10},
            ),
            {"command": "addBill", "worktableId": "Bench_1", "recipeDef": "MakeMealSimple", "repeatMode": "untilX", "targetCount": 10},
        )
        self.assertEqual(
            tool_call_to_bridge_command(
                "assign_allowed_area", {"pawn_id": "Pawn_1", "area_id": None}
            ),
            {"command": "assignAllowedArea", "pawnId": "Pawn_1", "areaId": None},
        )
        self.assertEqual(
            tool_call_to_bridge_command(
                "prioritize_construct", {"pawn_id": "Pawn_1", "blueprint_or_frame_id": "Blueprint_1"}
            ),
            {"command": "prioritizeConstruct", "pawnId": "Pawn_1", "blueprintOrFrameId": "Blueprint_1"},
        )


if __name__ == "__main__":
    unittest.main()
