import copy
import json
import tempfile
import unittest
from pathlib import Path

from state_diff import StateDiff, serialized_chars
from state_store import StateStore


def cells(count=60):
    return [{"x": 10 + index % 10, "z": 20 + index // 10} for index in range(count)]


def pawn(pawn_id="Pawn_A"):
    return {
        "id": pawn_id,
        "name": "Ava" if pawn_id == "Pawn_A" else "Bea",
        "kindDef": "Colonist",
        "position": {"x": 50, "z": 50},
        "drafted": False,
        "currentJob": {"defName": "Research", "label": "Researching"},
        "health": {"summary": "healthy", "downed": False, "dead": False, "bleedingRate": 0.0, "pain": 0.0, "hediffs": []},
        "needs": {"mood": 0.6, "food": 0.72, "rest": 0.7, "recreation": 0.6},
        "work": [
            {"defName": "Research", "label": "Research", "capable": True, "disabled": False, "disabledReason": None, "priority": 3},
            {"defName": "Hauling", "label": "Haul", "capable": True, "disabled": False, "disabledReason": None, "priority": 2},
        ],
        "skills": [{"defName": "Intellectual", "level": 8, "passion": "Major"}],
        "primaryEquipment": None,
        "equipment": [],
        "apparel": [],
        "assignedBed": None,
        "allowedArea": None,
    }


def base_state(version=120, ticks=1000, lineage="lineage-a", schema=2):
    zone_cells = cells()
    return {
        "schemaVersion": schema,
        "snapshot": {"version": version, "ticksGame": ticks, "capturedAtUtc": "2026-01-01T00:00:00Z"},
        "game": {"loaded": True, "paused": False, "speed": 1, "ticksGame": ticks, "currentMapId": "map-7", "colonyLineageId": lineage},
        "colony": {
            "colonistCount": 1,
            "prisonerCount": 0,
            "animalCount": 0,
            "wealth": {"total": 18432, "itemValue": 7200, "buildingValue": 6400, "pawnValue": 4832},
        },
        "resources": {
            "wood": 180,
            "steel": 450,
            "food": {"meals": 10, "totalNutrition": 50.0},
            "available": {"wood": 180, "steel": 450, "food": {"meals": 10, "totalNutrition": 50.0}},
            "forbidden": {"wood": 0, "steel": 0, "food": {"meals": 0, "totalNutrition": 0.0}},
            "totalVisible": {"wood": 180, "steel": 450, "food": {"meals": 10, "totalNutrition": 50.0}},
        },
        "colonists": [pawn()],
        "research": {
            "current": {"defName": "Electricity", "label": "Electricity", "progress": 100.0, "cost": 1600.0},
            "available": [{"defName": "Electricity", "label": "Electricity", "progress": 100.0, "cost": 1600.0}],
        },
        "threats": [],
        "mapThings": {"forbidden": [], "haulable": []},
        "map": {
            "id": "map-7",
            "width": 250,
            "height": 250,
            "colonyCenter": {"x": 50, "z": 50},
            "homeAreaBounds": {"minX": 40, "minZ": 40, "maxX": 70, "maxZ": 70},
            "environment": {"outdoorTemperature": 21.0, "season": "Aprimay", "growingSeason": True, "weather": "Clear"},
            "pointsOfInterest": [],
            "zones": [{"id": "zone-1", "type": "growing", "label": "Growing zone 1", "cellCount": 60, "bounds": {"minX": 10, "minZ": 20, "maxX": 19, "maxZ": 25}, "plantDef": "Plant_Potato", "cells": zone_cells}],
        },
        "buildings": [{"id": "Building_Bench", "type": "building", "defName": "SimpleResearchBench", "label": "research bench", "position": {"x": 55, "z": 52}, "rotation": "North", "hitPoints": 100, "maxHitPoints": 100, "powered": None}],
        "plants": [{"id": f"Plant_{index}", "defName": "Plant_Potato", "position": cell, "growth": 0.4, "mature": False, "harvestableNow": False} for index, cell in enumerate(zone_cells)],
        "operations": {
            "equipment": {"availableWeapons": [{"id": "Gun_1", "defName": "Gun_Revolver", "label": "revolver", "quality": "Normal", "hitPoints": 100, "maxHitPoints": 100, "forbidden": False, "reserved": False, "position": {"x": 48, "z": 48}}]},
            "apparel": {"available": [{"id": "Jacket_1", "defName": "Apparel_Jacket", "label": "jacket", "quality": "Normal", "hitPoints": 100, "maxHitPoints": 100, "wornBy": None, "tainted": False, "forbidden": False, "position": {"x": 48, "z": 49}}]},
            "beds": [{"id": "Bed_1", "defName": "Bed", "label": "bed", "position": {"x": 52, "z": 52}, "medical": False, "forPrisoners": False, "owners": []}],
            "worktables": [{"id": "Building_Bench", "defName": "SimpleResearchBench", "label": "research bench", "position": {"x": 55, "z": 52}, "powered": None, "fueled": None, "operational": True, "bills": []}],
            "power": {"networks": [{"id": "powerNet-0", "generationWatts": 1000.0, "consumptionWatts": 500.0, "netWatts": 500.0, "storedEnergyWd": 300.0, "batteryCapacityWd": 600.0, "connectedBuildings": [{"id": "Generator_1", "defName": "WoodFiredGenerator", "powerType": "generator", "connected": True, "powerOn": True}]}], "unpoweredBuildings": []},
            "fuel": [{"id": "Generator_1", "defName": "WoodFiredGenerator", "label": "wood-fired generator", "position": {"x": 60, "z": 60}, "fuel": 50.0, "fuelCapacity": 75.0, "targetFuelLevel": 75.0, "needsFuel": False}],
            "allowedAreas": [{"id": "Area_1", "label": "Safe", "cellCount": 60, "cells": zone_cells}],
        },
    }


def changed_state(baseline):
    result = copy.deepcopy(baseline)
    result["snapshot"]["version"] += 1
    result["snapshot"]["ticksGame"] += 60
    result["game"]["ticksGame"] += 60
    return result


class StateDiffTests(unittest.TestCase):
    def test_identical_except_snapshot_is_tiny(self):
        baseline = base_state()
        current = changed_state(baseline)
        delta = StateDiff.compare(baseline, current)
        self.assertEqual(delta["changes"], {})
        self.assertLess(serialized_chars(delta), 120)

    def test_resource_change_emits_only_changed_resource(self):
        baseline, current = base_state(), base_state(version=121)
        current["resources"]["available"]["wood"] = 425
        current["resources"]["totalVisible"]["wood"] = 425
        delta = StateDiff.compare(baseline, current)["changes"]["resources"]
        self.assertEqual(delta["available"]["wood"], {"from": 180, "to": 425})
        self.assertNotIn("steel", json.dumps(delta))

    def test_resource_array_and_entity_reorder_are_ignored(self):
        baseline, current = base_state(), base_state(version=121)
        resources = [{"defName": "WoodLog", "count": 10}, {"defName": "Steel", "count": 20}]
        baseline["resources"]["stored"] = resources
        current["resources"]["stored"] = list(reversed(resources))
        baseline["colonists"].append(pawn("Pawn_B"))
        current["colonists"] = [pawn("Pawn_B"), pawn()]
        baseline["buildings"].append({"id": "Building_2", "type": "building", "defName": "Wall", "position": {"x": 1, "z": 1}})
        current["buildings"] = list(reversed(baseline["buildings"]))
        delta = StateDiff.compare(baseline, current)
        self.assertEqual(delta["changes"], {})

    def test_work_priority_change_is_field_level(self):
        baseline, current = base_state(), base_state(version=121)
        current["colonists"][0]["work"][0]["priority"] = 1
        changed = StateDiff.compare(baseline, current)["changes"]["colonists"]["changed"][0]
        self.assertEqual(changed["work"], {"Research": {"priority": {"from": 3, "to": 1}}})
        self.assertNotIn("Hauling", changed["work"])

    def test_need_micro_change_is_omitted_but_threshold_crossing_is_emitted(self):
        baseline, current = base_state(), base_state(version=121)
        current["colonists"][0]["needs"]["food"] = 0.70
        self.assertEqual(StateDiff.compare(baseline, current)["changes"], {})
        current["colonists"][0]["needs"]["food"] = 0.20
        need = StateDiff.compare(baseline, current)["changes"]["colonists"]["changed"][0]["needs"]["food"]
        self.assertEqual(need["from"]["band"], "adequate")
        self.assertEqual(need["to"]["band"], "low")

    def test_health_emergencies_always_survive(self):
        baseline, current = base_state(), base_state(version=121)
        health = current["colonists"][0]["health"]
        health.update({"summary": "downed", "downed": True, "bleedingRate": 0.6, "pain": 0.7})
        delta = StateDiff.compare(baseline, current)["changes"]["colonists"]["changed"][0]["health"]
        self.assertEqual(delta["downed"]["to"], True)
        self.assertEqual(delta["bleedingRate"]["to"]["band"], "critical")

    def test_pawn_join_and_removal_are_stable_id_events(self):
        baseline, current = base_state(), base_state(version=121)
        current["colonists"].append(pawn("Pawn_B"))
        joined = StateDiff.compare(baseline, current)["changes"]["colonists"]["added"][0]
        self.assertEqual(joined["id"], "Pawn_B")
        after_removal = changed_state(current)
        after_removal["colonists"] = [pawn()]
        removed = StateDiff.compare(current, after_removal)["changes"]["colonists"]["removed"][0]
        self.assertEqual(removed["id"], "Pawn_B")

    def test_equipment_apparel_and_bed_assignment_are_compact(self):
        baseline, current = base_state(), base_state(version=121)
        colonist = current["colonists"][0]
        colonist["primaryEquipment"] = {"id": "Gun_1", "defName": "Gun_Revolver", "label": "revolver"}
        colonist["apparel"] = [{"id": "Jacket_1", "defName": "Apparel_Jacket", "label": "jacket"}]
        colonist["assignedBed"] = {"id": "Bed_1", "defName": "Bed", "position": {"x": 52, "z": 52}}
        changed = StateDiff.compare(baseline, current)["changes"]["colonists"]["changed"][0]
        self.assertEqual(changed["primaryEquipment"]["to"]["id"], "Gun_1")
        self.assertEqual(changed["apparel"]["added"][0]["id"], "Jacket_1")
        self.assertEqual(changed["assignedBed"]["to"]["id"], "Bed_1")

    def test_bill_addition_and_target_change_do_not_dump_operations(self):
        baseline, current = base_state(), base_state(version=121)
        bill = {"id": "Bill_1", "recipeDef": "MakeMealSimple", "label": "Make simple meal", "repeatMode": "untilX", "targetCount": 10, "repeatCount": 1, "suspended": False}
        current["operations"]["worktables"][0]["bills"].append(bill)
        bills = StateDiff.compare(baseline, current)["changes"]["operations"]["worktables"]["billChanges"][0]["bills"]
        self.assertEqual(bills["added"][0]["id"], "Bill_1")
        baseline = current
        current = changed_state(current)
        current["operations"]["worktables"][0]["bills"][0]["targetCount"] = 20
        bills = StateDiff.compare(baseline, current)["changes"]["operations"]["worktables"]["billChanges"][0]["bills"]
        self.assertEqual(bills["changed"][0]["targetCount"], {"from": 10, "to": 20})

    def test_bill_giver_movement_is_ignored_but_operational_change_emits(self):
        baseline, current = base_state(), base_state(version=121)
        current["operations"]["worktables"][0]["position"] = {"x": 80, "z": 80}
        self.assertNotIn("operations", StateDiff.compare(baseline, current)["changes"])

        current["operations"]["worktables"][0]["operational"] = False
        changed = StateDiff.compare(baseline, current)["changes"]["operations"]["worktables"]["changed"][0]
        self.assertEqual(changed["operational"], {"from": True, "to": False})

    def test_power_and_fuel_thresholds_emit_while_micro_changes_do_not(self):
        baseline, micro = base_state(), base_state(version=121)
        micro["operations"]["power"]["networks"][0]["netWatts"] = 499.0
        micro["operations"]["fuel"][0]["fuel"] = 49.5
        self.assertNotIn("power", StateDiff.compare(baseline, micro)["changes"].get("operations", {}))
        self.assertNotIn("fuel", StateDiff.compare(baseline, micro)["changes"].get("operations", {}))

        critical = base_state(version=121)
        network = critical["operations"]["power"]["networks"][0]
        network.update({"generationWatts": 0.0, "netWatts": -500.0})
        critical["operations"]["fuel"][0]["fuel"] = 0.0
        operations = StateDiff.compare(baseline, critical)["changes"]["operations"]
        self.assertEqual(operations["power"]["changed"][0]["status"]["to"], "offline")
        self.assertEqual(operations["fuel"]["changed"][0]["fuel"]["to"]["band"], "empty")

    def test_threat_added_and_resolved_are_never_noise_filtered(self):
        baseline, current = base_state(), base_state(version=121)
        threat = {"id": "Raider_1", "type": "pawn", "defName": "Human", "label": "raider", "faction": "Pirates", "position": {"x": 100, "z": 100}, "downed": False, "weapon": {"defName": "Gun_Pistol"}}
        current["threats"].append(threat)
        self.assertEqual(StateDiff.compare(baseline, current)["changes"]["threats"]["added"][0]["id"], "Raider_1")
        resolved = changed_state(current)
        resolved["threats"] = []
        self.assertEqual(StateDiff.compare(current, resolved)["changes"]["threats"]["resolved"][0]["id"], "Raider_1")

    def test_research_micro_progress_is_omitted_and_completion_is_emitted(self):
        baseline, current = base_state(), base_state(version=121)
        current["research"]["current"]["progress"] = 110.0
        current["research"]["available"][0]["progress"] = 110.0
        self.assertNotIn("research", StateDiff.compare(baseline, current)["changes"])
        current["research"] = {"current": None, "available": []}
        research = StateDiff.compare(baseline, current)["changes"]["research"]
        self.assertIn("completed", research)

    def test_zone_geometry_plant_change_and_large_unchanged_cells(self):
        baseline, current = base_state(), base_state(version=121)
        self.assertNotIn("map", StateDiff.compare(baseline, current)["changes"])
        current["map"]["zones"][0]["plantDef"] = "Plant_Rice"
        zone = StateDiff.compare(baseline, current)["changes"]["map"]["zones"]["changed"][0]
        self.assertEqual(zone["plantDef"]["to"], "Plant_Rice")
        self.assertNotIn("geometry", zone)
        current = base_state(version=121)
        current["map"]["zones"][0]["cells"] = current["map"]["zones"][0]["cells"][1:] + [{"x": 30, "z": 30}, {"x": 31, "z": 30}]
        geometry = StateDiff.compare(baseline, current)["changes"]["map"]["zones"]["changed"][0]["geometry"]
        self.assertEqual(len(geometry["addedCells"]), 2)
        self.assertEqual(len(geometry["removedCells"]), 1)

    def test_plant_growth_noise_is_omitted_and_harvest_event_emitted(self):
        baseline, current = base_state(), base_state(version=121)
        for plant in current["plants"]:
            plant["growth"] += 0.01
        self.assertNotIn("plants", StateDiff.compare(baseline, current)["changes"])
        current["plants"][0].update({"growth": 1.0, "mature": True, "harvestableNow": True})
        plants = StateDiff.compare(baseline, current)["changes"]["plants"]
        self.assertEqual(plants["becameHarvestable"][0]["id"], "Plant_0")

    def test_blueprint_added_completed_and_building_destroyed(self):
        baseline, current = base_state(), base_state(version=121)
        blueprint = {"id": "Blueprint_Wall_1", "type": "blueprint", "defName": "Blueprint_Wall", "label": "wall blueprint", "position": {"x": 70, "z": 70}, "rotation": "North"}
        current["buildings"].append(blueprint)
        construction = StateDiff.compare(baseline, current)["changes"]["construction"]
        self.assertEqual(construction["added"][0]["id"], "Blueprint_Wall_1")
        baseline = current
        current = changed_state(current)
        current["buildings"] = [item for item in current["buildings"] if item["id"] != "Blueprint_Wall_1"]
        current["buildings"].append({"id": "Wall_1", "type": "building", "defName": "Wall", "label": "wall", "position": {"x": 70, "z": 70}, "rotation": "North"})
        transitions = StateDiff.compare(baseline, current)["changes"]["construction"]["transitions"]
        self.assertEqual(transitions[0]["from"]["type"], "blueprint")
        self.assertEqual(transitions[0]["to"]["type"], "building")
        destroyed = changed_state(current)
        destroyed["buildings"] = [item for item in destroyed["buildings"] if item["id"] != "Wall_1"]
        self.assertEqual(StateDiff.compare(current, destroyed)["changes"]["construction"]["removed"][0]["id"], "Wall_1")

    def test_allowed_area_assignment_and_geometry_are_compact(self):
        baseline, current = base_state(), base_state(version=121)
        current["colonists"][0]["allowedArea"] = {"id": "Area_1", "label": "Safe"}
        current["operations"]["allowedAreas"][0]["cells"].append({"x": 99, "z": 99})
        changes = StateDiff.compare(baseline, current)["changes"]
        self.assertEqual(changes["colonists"]["changed"][0]["allowedArea"]["to"]["id"], "Area_1")
        geometry = changes["operations"]["allowedAreas"]["changed"][0]["geometry"]
        self.assertEqual(geometry["addedCells"], [{"x": 99, "z": 99}])

    def test_invalid_baselines_require_bootstrap(self):
        current = base_state()
        self.assertEqual(StateDiff.compare(None, current)["reason"], "missingDecisionBaseline")
        self.assertEqual(StateDiff.compare(base_state(lineage="other"), current)["reason"], "colonyIdentityChanged")
        self.assertEqual(StateDiff.compare(base_state(schema=3), current)["reason"], "incompatibleStateSchema")

    def test_safe_bridge_snapshot_counter_reset_keeps_semantic_delta(self):
        baseline = base_state(version=120, ticks=1_000)
        current = base_state(version=1, ticks=1_010)
        current["resources"]["available"]["wood"] = 200
        delta = StateDiff.compare(baseline, current)
        self.assertFalse(delta.get("bootstrapRequired", False))
        self.assertTrue(delta["snapshotStreamReset"])
        self.assertEqual(delta["changes"]["resources"]["available"]["wood"], {"from": 180, "to": 200})

    def test_regressing_ticks_with_reset_counter_remains_ambiguous(self):
        baseline = base_state(version=120, ticks=1_000)
        current = base_state(version=1, ticks=999)
        self.assertEqual(StateDiff.compare(baseline, current)["reason"], "snapshotLineageChanged")

    def test_diff_is_deterministic_and_does_not_mutate_inputs(self):
        baseline, current = base_state(), base_state(version=121)
        current["resources"]["available"]["wood"] = 200
        before_copy, after_copy = copy.deepcopy(baseline), copy.deepcopy(current)
        first = json.dumps(StateDiff.compare(baseline, current), sort_keys=True, separators=(",", ":"))
        second = json.dumps(StateDiff.compare(baseline, current), sort_keys=True, separators=(",", ":"))
        self.assertEqual(first, second)
        self.assertEqual(baseline, before_copy)
        self.assertEqual(current, after_copy)

    def test_large_change_is_bounded_and_preserves_critical_threat(self):
        baseline, current = base_state(), base_state(version=121)
        current["buildings"].extend({"id": f"Building_{index}", "type": "building", "defName": "Wall", "position": {"x": index, "z": 2}} for index in range(200))
        current["threats"].append({"id": "Raider_critical", "type": "pawn", "defName": "Human", "label": "raider", "position": {"x": 1, "z": 1}, "downed": False})
        delta = StateDiff.compare(baseline, current, max_entity_details=10, max_delta_chars=2500)
        self.assertTrue(delta["changes"]["construction"]["addedDetailsTruncated"])
        self.assertEqual(delta["changes"]["threats"]["added"][0]["id"], "Raider_critical")
        self.assertLess(serialized_chars(delta), 2500)

    def test_extreme_critical_change_obeys_hard_bound_with_explicit_summary(self):
        baseline, current = base_state(), base_state(version=121)
        current["colonists"] = []
        current["threats"] = [
            {
                "id": f"Raider_{index:04d}",
                "type": "pawn",
                "defName": "Human",
                "label": "raider " + ("x" * 300),
                "position": {"x": index, "z": 100},
                "downed": False,
            }
            for index in range(500)
        ]
        delta = StateDiff.compare(baseline, current, max_entity_details=100, max_delta_chars=1500)
        self.assertLessEqual(serialized_chars(delta), 1500)
        self.assertTrue(delta["detailsTruncated"])
        self.assertGreater(delta["changes"]["threats"]["changeCount"], 0)
        self.assertTrue(delta["changes"]["threats"]["detailsTruncated"])

    def test_state_store_delta_does_not_advance_baseline_and_logs_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = []
            store = StateStore(Path(directory), logger=logs.append)
            baseline = base_state()
            current = base_state(version=121)
            current["resources"]["available"]["wood"] = 200
            store.update_current_state(baseline)
            store.set_decision_baseline(baseline)
            store.update_current_state(current)
            delta = store.get_changes_since_last_decision()
            self.assertIn("resources", delta["changes"])
            self.assertEqual(store.get_decision_baseline()["snapshot"]["version"], 120)
            self.assertTrue(any(message.startswith("[DELTA]") for message in logs))


if __name__ == "__main__":
    unittest.main()
