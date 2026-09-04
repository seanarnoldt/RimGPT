import unittest

from tools import is_read_only_tool, tool_call_to_bridge_command


class ToolV2BTests(unittest.TestCase):
    def test_read_only_tools_are_identified(self) -> None:
        self.assertTrue(is_read_only_tool("inspect_map"))
        self.assertTrue(is_read_only_tool("list_build_options"))
        self.assertTrue(is_read_only_tool("get_build_info"))
        self.assertTrue(is_read_only_tool("list_growable_plants"))
        self.assertFalse(is_read_only_tool("create_stockpile"))

    def test_create_stockpile_mapping(self) -> None:
        self.assertEqual(
            tool_call_to_bridge_command(
                "create_stockpile",
                {"min_x": 10, "min_z": 11, "max_x": 12, "max_z": 13},
            ),
            {"command": "createStockpile", "minX": 10, "minZ": 11, "maxX": 12, "maxZ": 13},
        )

    def test_place_blueprints_mapping(self) -> None:
        self.assertEqual(
            tool_call_to_bridge_command(
                "place_blueprints",
                {
                    "placements": [
                        {
                            "build_def": "Wall",
                            "x": 20,
                            "z": 21,
                            "rotation": "North",
                            "stuff_def": "WoodLog",
                        }
                    ]
                },
            ),
            {
                "command": "placeBlueprints",
                "placements": [
                    {
                        "buildDef": "Wall",
                        "x": 20,
                        "z": 21,
                        "rotation": "North",
                        "stuffDef": "WoodLog",
                    }
                ],
            },
        )

    def test_zone_and_cancel_mappings(self) -> None:
        self.assertEqual(
            tool_call_to_bridge_command("set_growing_zone_plant", {"zone_id": "zone-1", "plant_def": "Plant_Rice"}),
            {"command": "setGrowingZonePlant", "zoneId": "zone-1", "plantDef": "Plant_Rice"},
        )
        self.assertEqual(
            tool_call_to_bridge_command("cancel_at", {"x": 5, "z": 6}),
            {"command": "cancelAt", "x": 5, "z": 6},
        )


if __name__ == "__main__":
    unittest.main()
