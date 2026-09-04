import unittest

from tools import tool_call_to_bridge_command


class ToolMappingV2ATests(unittest.TestCase):
    def test_allow_and_forbid_mapping(self):
        self.assertEqual(
            tool_call_to_bridge_command("allow", {"thing_id": "Thing_Steel123"}),
            {"command": "allow", "thingId": "Thing_Steel123"},
        )
        self.assertEqual(
            tool_call_to_bridge_command("forbid", {"thing_id": "Thing_Steel123"}),
            {"command": "forbid", "thingId": "Thing_Steel123"},
        )

    def test_allow_all_mapping(self):
        self.assertEqual(tool_call_to_bridge_command("allow_all", {}), {"command": "allowAll"})

    def test_research_and_prioritize_mapping(self):
        self.assertEqual(
            tool_call_to_bridge_command("set_research", {"research_def": "MicroelectronicsBasics"}),
            {"command": "setResearch", "researchDef": "MicroelectronicsBasics"},
        )
        self.assertEqual(
            tool_call_to_bridge_command(
                "prioritize_job",
                {"pawn_id": "Human123", "target_id": "Thing456"},
            ),
            {"command": "prioritizeJob", "pawnId": "Human123", "targetId": "Thing456"},
        )

    def test_designation_mappings(self):
        self.assertEqual(
            tool_call_to_bridge_command("designate_mine", {"x": 120, "z": 84}),
            {"command": "designateMine", "x": 120, "z": 84},
        )
        self.assertEqual(
            tool_call_to_bridge_command("designate_cut", {"x": 120, "z": 84}),
            {"command": "designateCut", "x": 120, "z": 84},
        )
        self.assertEqual(
            tool_call_to_bridge_command("designate_harvest", {"x": 120, "z": 84}),
            {"command": "designateHarvest", "x": 120, "z": 84},
        )
        self.assertEqual(
            tool_call_to_bridge_command("designate_hunt", {"thing_id": "Animal123"}),
            {"command": "designateHunt", "thingId": "Animal123"},
        )


if __name__ == "__main__":
    unittest.main()
