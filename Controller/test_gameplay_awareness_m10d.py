import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_controller import SYSTEM_INSTRUCTIONS
from decision_context import DecisionContextBuilder, build_current_summary, serialize_context
from model_tool_result import ModelToolResultFormatter, decode_inspect_map
from prompt_runtime import RIMGPT_PROMPT_VERSION, build_prompt_cache_key
from state_diff import StateDiff
from state_store import StateStore
from test_state_diff import base_state


def awareness(event_count=1):
    events = [
        {
            "id": f"message-Message_{index}",
            "type": "ThreatBig",
            "severity": "High",
            "title": "Threat",
            "text": "Ancient danger",
            "ticksGame": 1200 + index,
            "hiddenTarget": "must-not-reach-model-context",
        }
        for index in range(event_count)
    ]
    return {"activeAlerts": [], "activeLetters": [], "recentEvents": events}


def inspect_result(room):
    return {
        "gameLoaded": True,
        "mapId": "map-7",
        "bounds": {"minX": 10, "minZ": 10, "maxX": 10, "maxZ": 10},
        "terrainRows": [{
            "z": 10,
            "runs": [{
                "x": 10,
                "len": 1,
                "cell": {
                    "terrain": "Soil",
                    "terrainLabel": "soil",
                    "fertility": 1.0,
                    "walkable": True,
                    "buildable": True,
                    "roofed": bool(room and room.get("roofCoverage", 0) > 0),
                    "water": False,
                    "canCreateGrowingZone": True,
                    "canCreateStockpile": True,
                    "affordances": ["Light", "Medium", "Heavy"],
                    "room": room,
                },
            }],
        }],
        "things": [],
        "zones": [],
    }


class GameplayAwarenessM10DTests(unittest.TestCase):
    def test_ancient_danger_reaches_summary_trigger_and_delta_without_hidden_payload(self):
        baseline = base_state()
        baseline["awareness"] = awareness(0)
        current = base_state(version=121)
        current["awareness"] = awareness(1)
        delta = StateDiff.compare(baseline, current)
        self.assertEqual(delta["changes"]["awareness"]["newEvents"][0]["text"], "Ancient danger")

        with tempfile.TemporaryDirectory() as directory:
            logs = []
            store = StateStore(Path(directory), logger=logs.append)
            store.update_current_state(baseline)
            store.set_decision_baseline(baseline)
            store.update_current_state(current)
            context = DecisionContextBuilder(store, logger=logs.append).build()
        self.assertEqual(context["currentSummary"]["awareness"]["status"], "attentionRequired")
        self.assertTrue(context["trigger"]["playerAwareness"]["changed"])
        self.assertNotIn("must-not-reach-model-context", serialize_context(context))
        self.assertTrue(any("fullStateSent=false" in line for line in logs))

    def test_awareness_summary_is_bounded_and_source_history_has_hard_limit(self):
        state = base_state()
        state["awareness"] = awareness(50)
        self.assertEqual(len(build_current_summary(state)["awareness"]["important"]), 8)
        source = (Path(__file__).parent.parent / "Source" / "RimGPTPlayerAwarenessJson.cs").read_text()
        self.assertIn("MaxRecentEvents = 24", source)
        self.assertIn("MaxSeenEventIds = 256", source)
        self.assertNotIn("lookTargets", source)

    def test_awareness_added_to_legacy_baseline_is_semantic_and_survives_compaction(self):
        baseline = base_state()
        current = base_state(version=121)
        current["awareness"] = awareness(1)
        delta = StateDiff.compare(baseline, current, max_delta_chars=1500)
        self.assertEqual(delta["changes"]["awareness"]["newEvents"][0]["text"], "Ancient danger")
        self.assertNotIn("from", delta["changes"]["awareness"])

    def test_unenclosed_room_is_explicitly_unsuitable_for_temperature_control(self):
        room = {
            "id": "room-7-1",
            "indoors": False,
            "enclosed": False,
            "usesOutdoorTemperature": True,
            "suitableForTemperatureControl": False,
            "cellCount": 80,
            "roofedCellCount": 10,
            "roofCoverage": 0.125,
            "temperature": 42.0,
            "bounds": {"minX": 1, "minZ": 1, "maxX": 20, "maxZ": 20},
        }
        compact = ModelToolResultFormatter(logger=lambda _: None).format("inspect_map", inspect_result(room), {})
        decoded = decode_inspect_map(compact)
        self.assertFalse(decoded[(10, 10)]["room"]["suitableForTemperatureControl"])
        self.assertFalse(decoded[(10, 10)]["room"]["enclosed"])

    def test_enclosed_room_data_survives_map_result_compression(self):
        room = {
            "id": "room-7-2",
            "indoors": True,
            "enclosed": True,
            "usesOutdoorTemperature": False,
            "suitableForTemperatureControl": True,
            "cellCount": 25,
            "roofedCellCount": 25,
            "roofCoverage": 1.0,
            "temperature": 21.5,
            "bounds": {"minX": 8, "minZ": 8, "maxX": 12, "maxZ": 12},
        }
        compact = ModelToolResultFormatter(logger=lambda _: None).format("inspect_map", inspect_result(room), {})
        preserved = decode_inspect_map(compact)[(10, 10)]["room"]
        self.assertTrue(preserved["suitableForTemperatureControl"])
        self.assertEqual(preserved["temperature"], 21.5)
        self.assertEqual(preserved["bounds"]["maxX"], 12)

    def test_heatstroke_and_idle_capable_labor_are_visible_in_summary(self):
        state = base_state()
        state["colonists"][0]["health"]["hediffs"] = [
            {"defName": "Heatstroke", "label": "heatstroke", "severity": 0.42}
        ]
        state["operations"]["labor"] = {
            "idleColonistCount": 1,
            "capableIdleColonistCount": 1,
            "pendingWork": {"blueprints": 4, "haulables": 12},
            "obviousBlockers": [{"category": "research", "reason": "active research has no usable player research bench"}],
        }
        summary = build_current_summary(state)
        self.assertEqual(summary["health"]["urgentConditions"][0]["defName"], "Heatstroke")
        self.assertEqual(summary["labor"]["capableIdleColonists"], 1)
        self.assertEqual(summary["labor"]["pendingWork"]["blueprints"], 4)

    def test_prompt_requires_actionable_prerequisites_and_preserves_m10d_guidance(self):
        self.assertIn("convert the blocker into an executable prerequisite", SYSTEM_INSTRUCTIONS)
        self.assertIn("preserve the parent goal as an open loop", SYSTEM_INSTRUCTIONS)
        self.assertIn("temperature-sensitive shelter", SYSTEM_INSTRUCTIONS)
        self.assertEqual(RIMGPT_PROMPT_VERSION, "context-memory-v1-m10h1")
        self.assertEqual(build_prompt_cache_key("gpt-5.6"), "rimgpt:context-memory-v1-m10h1:gpt-5.6")


if __name__ == "__main__":
    unittest.main()
