using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using RimWorld;
using UnityEngine;
using Verse;

namespace RimGPT
{
    public static class RimGPTSpatialJson
    {
        private const int MaxRegionWidth = 40;
        private const int MaxRegionHeight = 40;
        private const int MaxRegionThings = 240;
        private const int MaxOverviewPoints = 40;
        private const int MaxOverviewZones = 40;
        private const int MaxStateBuildings = 160;
        private const int MaxZoneCheckCells = 400;

        public static string BuildMapOverviewJson(Map map)
        {
            StringBuilder json = new StringBuilder(4096);
            json.Append("{");
            WriteString(json, "id", "map-" + map.uniqueID, false);
            WriteInt(json, "width", map.Size.x, true);
            WriteInt(json, "height", map.Size.z, true);
            WritePosition(json, "colonyCenter", ColonyCenter(map), true);
            WriteHomeAreaBounds(json, map, true);
            WriteEnvironment(json, map, true);
            WritePointsOfInterest(json, map, true);
            WriteZones(json, map, true);
            json.Append("}");
            return json.ToString();
        }

        public static string BuildBuildingsJson(Map map)
        {
            StringBuilder json = new StringBuilder(8192);
            json.Append("[");
            int written = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count && written < MaxStateBuildings; i++)
            {
                Thing thing = things[i];
                if (!IsVisible(thing, map) || !IsRelevantBuildingStateThing(thing))
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteThingObject(json, thing, map);
                written++;
            }
            json.Append("]");
            return json.ToString();
        }

        public static string BuildMapRegionJson(int minX, int minZ, int maxX, int maxZ)
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return "{\"gameLoaded\":false}";
            }

            Map map = Find.CurrentMap;
            if (minX > maxX)
            {
                int temp = minX;
                minX = maxX;
                maxX = temp;
            }

            if (minZ > maxZ)
            {
                int temp = minZ;
                minZ = maxZ;
                maxZ = temp;
            }

            int width = maxX - minX + 1;
            int height = maxZ - minZ + 1;
            if (width > MaxRegionWidth || height > MaxRegionHeight)
            {
                return "{\"error\":\"regionTooLarge\",\"maxWidth\":" + MaxRegionWidth + ",\"maxHeight\":" + MaxRegionHeight + "}";
            }

            minX = Math.Max(0, minX);
            minZ = Math.Max(0, minZ);
            maxX = Math.Min(map.Size.x - 1, maxX);
            maxZ = Math.Min(map.Size.z - 1, maxZ);

            StringBuilder json = new StringBuilder(32768);
            json.Append("{\"schemaVersion\":2,\"gameLoaded\":true");
            json.Append(",\"mapId\":\"map-").Append(map.uniqueID).Append("\"");
            json.Append(",\"bounds\":{");
            WriteInt(json, "minX", minX, false);
            WriteInt(json, "minZ", minZ, true);
            WriteInt(json, "maxX", maxX, true);
            WriteInt(json, "maxZ", maxZ, true);
            json.Append("}");
            WriteTerrainRuns(json, map, minX, minZ, maxX, maxZ, true);
            WriteRegionThings(json, map, minX, minZ, maxX, maxZ, true);
            WriteRegionZones(json, map, minX, minZ, maxX, maxZ, true);
            json.Append("}");
            return json.ToString();
        }

        private static void WriteTerrainRuns(StringBuilder json, Map map, int minX, int minZ, int maxX, int maxZ, bool comma)
        {
            WriteName(json, "terrainRows", comma);
            json.Append("[");
            for (int z = minZ; z <= maxZ; z++)
            {
                if (z > minZ)
                {
                    json.Append(",");
                }

                json.Append("{");
                WriteInt(json, "z", z, false);
                json.Append(",\"runs\":[");

                string lastKey = null;
                int runStart = minX;
                int runLen = 0;
                string runJson = null;

                for (int x = minX; x <= maxX; x++)
                {
                    IntVec3 cell = new IntVec3(x, 0, z);
                    string cellJson = TerrainCellJson(map, cell);
                    string key = cellJson;
                    if (lastKey == null)
                    {
                        lastKey = key;
                        runStart = x;
                        runLen = 1;
                        runJson = cellJson;
                    }
                    else if (key == lastKey || key.Equals(lastKey, StringComparison.Ordinal))
                    {
                        runLen++;
                    }
                    else
                    {
                        WriteTerrainRun(json, runStart, runLen, runJson, runStart > minX);
                        lastKey = key;
                        runStart = x;
                        runLen = 1;
                        runJson = cellJson;
                    }
                }

                if (runLen > 0)
                {
                    WriteTerrainRun(json, runStart, runLen, runJson, runStart > minX);
                }

                json.Append("]}");
            }
            json.Append("]");
        }

        private static string TerrainCellJson(Map map, IntVec3 cell)
        {
            if (!cell.InBounds(map) || cell.Fogged(map))
            {
                return "{\"fog\":true}";
            }

            TerrainDef terrain = cell.GetTerrain(map);
            bool roofed = map.roofGrid != null && map.roofGrid.Roofed(cell);
            bool walkable = cell.Walkable(map);
            bool water = terrain != null && terrain.defName != null && terrain.defName.IndexOf("Water", StringComparison.OrdinalIgnoreCase) >= 0;
            Thing edifice = cell.GetEdifice(map);
            Mineable mineable = cell.GetFirstMineable(map);

            StringBuilder json = new StringBuilder(160);
            json.Append("{");
            WriteString(json, "terrain", terrain != null ? terrain.defName : null, false);
            WriteString(json, "terrainLabel", terrain != null ? terrain.label : null, true);
            WriteFloat(json, "fertility", terrain != null ? terrain.fertility : 0f, true);
            WriteBool(json, "walkable", walkable, true);
            WriteBool(json, "buildable", walkable && edifice == null && mineable == null, true);
            WriteBool(json, "roofed", roofed, true);
            WriteBool(json, "water", water, true);
            WriteRoom(json, "room", RegionAndRoomQuery.RoomAt(cell, map), map, true);
            if (edifice != null)
            {
                WriteAdjacentRoomIds(json, map, cell, true);
            }
            string growingZoneReason;
            string stockpileZoneReason;
            WriteBool(json, "canCreateGrowingZone", RimGPTZoneUtility.CanCreateZoneCell(map, cell, RimGPTZonePlacementType.Growing, out growingZoneReason), true);
            WriteBool(json, "canCreateStockpile", RimGPTZoneUtility.CanCreateZoneCell(map, cell, RimGPTZonePlacementType.Stockpile, out stockpileZoneReason), true);
            WriteAffordances(json, map, cell, true);
            json.Append("}");
            return json.ToString();
        }

        public static string BuildCheckZonePlacementJson(string zoneTypeName, int minX, int minZ, int maxX, int maxZ)
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return "{\"gameLoaded\":false}";
            }

            RimGPTZonePlacementType zoneType;
            if (!RimGPTZoneUtility.TryParseZoneType(zoneTypeName, out zoneType))
            {
                return "{\"error\":\"unknownZoneType\"}";
            }

            Map map = Find.CurrentMap;
            RimGPTZoneUtility.NormalizeRect(ref minX, ref minZ, ref maxX, ref maxZ);
            int width = maxX - minX + 1;
            int height = maxZ - minZ + 1;
            if (width > MaxRegionWidth || height > MaxRegionHeight)
            {
                return "{\"error\":\"regionTooLarge\",\"maxWidth\":" + MaxRegionWidth + ",\"maxHeight\":" + MaxRegionHeight + "}";
            }

            int requested = Math.Max(0, width) * Math.Max(0, height);
            int valid = 0;
            int invalid = 0;
            bool foundValid = false;
            CellBounds validBounds = new CellBounds();
            Dictionary<string, int> reasons = new Dictionary<string, int>();
            List<IntVec3> cells = new List<IntVec3>();
            List<RimGPTZoneCellResult> checkedCells = new List<RimGPTZoneCellResult>();

            for (int x = minX; x <= maxX; x++)
            {
                for (int z = minZ; z <= maxZ; z++)
                {
                    IntVec3 cell = new IntVec3(x, 0, z);
                    string reason;
                    bool cellValid = RimGPTZoneUtility.CanCreateZoneCell(map, cell, zoneType, out reason);
                    if (checkedCells.Count < MaxZoneCheckCells)
                    {
                        checkedCells.Add(new RimGPTZoneCellResult { Cell = cell, Valid = cellValid, Reason = reason });
                    }

                    if (cellValid)
                    {
                        valid++;
                        validBounds.Include(cell, ref foundValid);
                        if (cells.Count < 240)
                        {
                            cells.Add(cell);
                        }
                    }
                    else
                    {
                        invalid++;
                        if (string.IsNullOrEmpty(reason))
                        {
                            reason = "Cell cannot be added to this zone";
                        }

                        int count;
                        reasons.TryGetValue(reason, out count);
                        reasons[reason] = count + 1;
                    }
                }
            }

            StringBuilder json = new StringBuilder(8192);
            json.Append("{\"schemaVersion\":2,\"gameLoaded\":true");
            WriteString(json, "zoneType", zoneType == RimGPTZonePlacementType.Growing ? "growing" : "stockpile", true);
            json.Append(",\"requestedBounds\":{");
            WriteInt(json, "minX", minX, false);
            WriteInt(json, "minZ", minZ, true);
            WriteInt(json, "maxX", maxX, true);
            WriteInt(json, "maxZ", maxZ, true);
            json.Append("}");
            WriteInt(json, "requestedCells", requested, true);
            WriteInt(json, "validCells", valid, true);
            WriteInt(json, "invalidCells", invalid, true);
            WriteInt(json, "cellLimit", MaxZoneCheckCells, true);
            WriteName(json, "validBounds", true);
            if (foundValid)
            {
                WriteBounds(json, validBounds);
            }
            else
            {
                json.Append("null");
            }

            WriteName(json, "validCellSample", true);
            json.Append("[");
            for (int i = 0; i < cells.Count; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                IntVec3 cell = cells[i];
                json.Append("{");
                WriteInt(json, "x", cell.x, false);
                WriteInt(json, "z", cell.z, true);
                TerrainDef terrain = cell.GetTerrain(map);
                WriteString(json, "terrain", terrain != null ? terrain.defName : null, true);
                WriteFloat(json, "fertility", terrain != null ? terrain.fertility : 0f, true);
                json.Append("}");
            }
            json.Append("]");

            WriteName(json, "cells", true);
            json.Append("[");
            for (int i = 0; i < checkedCells.Count; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                RimGPTZoneCellResult cellResult = checkedCells[i];
                json.Append("{");
                WriteInt(json, "x", cellResult.Cell.x, false);
                WriteInt(json, "z", cellResult.Cell.z, true);
                WriteBool(json, "valid", cellResult.Valid, true);
                WriteString(json, "reason", cellResult.Valid ? null : cellResult.Reason, true);
                json.Append("}");
            }
            json.Append("]");

            WriteName(json, "invalidReasons", true);
            json.Append("[");
            bool wroteReason = false;
            foreach (KeyValuePair<string, int> reason in reasons)
            {
                if (wroteReason)
                {
                    json.Append(",");
                }

                json.Append("{");
                WriteString(json, "reason", reason.Key, false);
                WriteInt(json, "count", reason.Value, true);
                json.Append("}");
                wroteReason = true;
            }
            json.Append("]}");
            return json.ToString();
        }

        private static void WriteAffordances(StringBuilder json, Map map, IntVec3 cell, bool comma)
        {
            WriteName(json, "affordances", comma);
            json.Append("[");
            bool wrote = false;
            List<TerrainAffordanceDef> affordances = null;
            try
            {
                affordances = cell.GetAffordances(map);
            }
            catch
            {
                TerrainDef terrain = cell.GetTerrain(map);
                affordances = terrain != null ? terrain.affordances : null;
            }

            if (affordances != null)
            {
                for (int i = 0; i < affordances.Count; i++)
                {
                    TerrainAffordanceDef affordance = affordances[i];
                    if (affordance == null)
                    {
                        continue;
                    }

                    if (wrote)
                    {
                        json.Append(",");
                    }

                    json.Append("\"").Append(RimGPTJson.Escape(affordance.defName)).Append("\"");
                    wrote = true;
                }
            }
            json.Append("]");
        }

        private static void WriteTerrainRun(StringBuilder json, int x, int len, string cellJson, bool comma)
        {
            if (comma)
            {
                json.Append(",");
            }

            json.Append("{");
            WriteInt(json, "x", x, false);
            WriteInt(json, "len", len, true);
            json.Append(",\"cell\":").Append(cellJson);
            json.Append("}");
        }

        private static void WriteRegionThings(StringBuilder json, Map map, int minX, int minZ, int maxX, int maxZ, bool comma)
        {
            WriteName(json, "things", comma);
            json.Append("[");
            int written = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count && written < MaxRegionThings; i++)
            {
                Thing thing = things[i];
                if (!IsVisible(thing, map) || thing.Position.x < minX || thing.Position.x > maxX || thing.Position.z < minZ || thing.Position.z > maxZ)
                {
                    continue;
                }

                if (!IsRegionRelevantThing(thing))
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteThingObject(json, thing, map);
                written++;
            }
            json.Append("]");
        }

        private static void WriteRegionZones(StringBuilder json, Map map, int minX, int minZ, int maxX, int maxZ, bool comma)
        {
            WriteName(json, "zones", comma);
            json.Append("[");
            int written = 0;
            List<Zone> zones = map.zoneManager.AllZones;
            for (int i = 0; i < zones.Count; i++)
            {
                Zone zone = zones[i];
                if (zone == null || !ZoneTouches(zone, minX, minZ, maxX, maxZ))
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteZone(json, zone);
                written++;
            }
            json.Append("]");
        }

        private static void WriteHomeAreaBounds(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "homeAreaBounds", comma);
            CellBounds bounds;
            if (!TryAreaBounds(map, out bounds))
            {
                json.Append("null");
                return;
            }

            WriteBounds(json, bounds);
        }

        private static void WriteEnvironment(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "environment", comma);
            json.Append("{");
            WriteFloat(json, "outdoorTemperature", SafeFloat(delegate { return map.mapTemperature.OutdoorTemp; }), false);
            WriteString(json, "season", SafeString(delegate { return GenDate.Season(Find.TickManager.TicksAbs, LongLat(map)).ToString(); }), true);
            WriteBool(json, "growingSeason", SafeBool(delegate {
                ThingDef rice = DefDatabase<ThingDef>.GetNamedSilentFail("Plant_Rice");
                return rice != null && PlantUtility.GrowthSeasonNow(map, rice);
            }), true);
            WriteString(json, "weather", SafeString(delegate { return map.weatherManager.curWeather != null ? map.weatherManager.curWeather.defName : null; }), true);
            json.Append("}");
        }

        private static void WritePointsOfInterest(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "pointsOfInterest", comma);
            json.Append("[");
            int written = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count && written < MaxOverviewPoints; i++)
            {
                Thing thing = things[i];
                if (!IsVisible(thing, map) || thing.def == null)
                {
                    continue;
                }

                string type = null;
                if (thing.def.defName.IndexOf("SteamGeyser", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    type = "steamGeyser";
                }
                else if (thing is Building && thing.Faction == Faction.OfPlayer)
                {
                    type = "existingBuilding";
                }

                if (type == null)
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                json.Append("{");
                WriteString(json, "type", type, false);
                WriteString(json, "id", SafeThingId(thing), true);
                WriteString(json, "defName", thing.def.defName, true);
                WriteString(json, "label", SafeLabel(thing), true);
                WritePosition(json, "position", thing.Position, true);
                json.Append("}");
                written++;
            }
            json.Append("]");
        }

        private static void WriteZones(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "zones", comma);
            json.Append("[");
            int count = 0;
            List<Zone> zones = map.zoneManager.AllZones;
            for (int i = 0; i < zones.Count && count < MaxOverviewZones; i++)
            {
                Zone zone = zones[i];
                if (zone == null)
                {
                    continue;
                }

                if (count > 0)
                {
                    json.Append(",");
                }

                WriteZone(json, zone);
                count++;
            }
            json.Append("]");
        }

        private static void WriteZone(StringBuilder json, Zone zone)
        {
            json.Append("{");
            WriteString(json, "id", ZoneId(zone), false);
            WriteString(json, "type", ZoneType(zone), true);
            WriteString(json, "label", zone.label, true);
            WriteInt(json, "cellCount", zone.CellCount, true);
            WriteName(json, "bounds", true);
            CellBounds bounds;
            if (TryZoneBounds(zone, out bounds))
            {
                WriteBounds(json, bounds);
            }
            else
            {
                json.Append("null");
            }

            Zone_Growing growing = zone as Zone_Growing;
            if (growing != null)
            {
                ThingDef plant = growing.GetPlantDefToGrow();
                json.Append(",\"plantDef\":");
                WriteStringValue(json, plant != null ? plant.defName : null);
            }
            json.Append("}");
        }

        private static void WriteThingObject(StringBuilder json, Thing thing, Map map)
        {
            json.Append("{");
            WriteString(json, "id", SafeThingId(thing), false);
            WriteString(json, "type", ThingType(thing), true);
            WriteString(json, "defName", thing.def != null ? thing.def.defName : null, true);
            WriteString(json, "label", SafeLabel(thing), true);
            WritePosition(json, "position", thing.Position, true);
            WriteString(json, "rotation", thing.Rotation.ToString(), true);
            WriteSize(json, thing.def, thing.Rotation, true);
            WriteInt(json, "stackCount", thing.stackCount, true);
            WriteInt(json, "hitPoints", thing.HitPoints, true);
            WriteInt(json, "maxHitPoints", thing.MaxHitPoints, true);
            WriteNullableBool(json, "powered", PoweredState(thing), true);
            WriteBool(json, "forbidden", thing.IsForbidden(Faction.OfPlayer), true);
            WriteRoom(json, "room", SafeRoom(delegate { return RegionAndRoomQuery.GetRoom(thing); }), map, true);
            WritePlantFields(json, thing);
            json.Append("}");
        }

        private static void WriteRoom(StringBuilder json, string name, Room room, Map map, bool comma)
        {
            WriteName(json, name, comma);
            if (room == null || room.Map != map || room.Fogged)
            {
                json.Append("null");
                return;
            }

            int cellCount = Math.Max(0, room.CellCount);
            int openRoofCount = Math.Max(0, room.OpenRoofCount);
            float roofCoverage = cellCount > 0 ? (float)(cellCount - openRoofCount) / cellCount : 0f;
            CellRect bounds = room.ExtentsClose;
            json.Append("{");
            WriteString(json, "id", "room-" + map.uniqueID + "-" + room.ID, false);
            WriteBool(json, "indoors", !room.PsychologicallyOutdoors, true);
            WriteBool(json, "enclosed", room.ProperRoom && !room.TouchesMapEdge, true);
            WriteBool(json, "usesOutdoorTemperature", room.UsesOutdoorTemperature, true);
            WriteBool(json, "suitableForTemperatureControl", room.ProperRoom && !room.TouchesMapEdge && !room.UsesOutdoorTemperature, true);
            WriteInt(json, "cellCount", cellCount, true);
            WriteInt(json, "roofedCellCount", Math.Max(0, cellCount - openRoofCount), true);
            WriteFloat(json, "roofCoverage", roofCoverage, true);
            WriteFloat(json, "temperature", room.Temperature, true);
            json.Append(",\"bounds\":{");
            WriteInt(json, "minX", bounds.minX, false);
            WriteInt(json, "minZ", bounds.minZ, true);
            WriteInt(json, "maxX", bounds.maxX, true);
            WriteInt(json, "maxZ", bounds.maxZ, true);
            json.Append("}}");
        }

        private static void WriteAdjacentRoomIds(StringBuilder json, Map map, IntVec3 cell, bool comma)
        {
            WriteName(json, "adjacentRoomIds", comma);
            json.Append("[");
            HashSet<int> seen = new HashSet<int>();
            bool wrote = false;
            for (int i = 0; i < GenAdj.CardinalDirections.Length; i++)
            {
                IntVec3 adjacent = cell + GenAdj.CardinalDirections[i];
                if (!adjacent.InBounds(map) || adjacent.Fogged(map))
                {
                    continue;
                }
                Room room = RegionAndRoomQuery.RoomAt(adjacent, map);
                if (room == null || room.Map != map || room.Fogged || !seen.Add(room.ID))
                {
                    continue;
                }
                if (wrote)
                {
                    json.Append(",");
                }
                WriteStringValue(json, "room-" + map.uniqueID + "-" + room.ID);
                wrote = true;
            }
            json.Append("]");
        }

        private static Room SafeRoom(Func<Room> getter)
        {
            try
            {
                return getter();
            }
            catch
            {
                return null;
            }
        }

        private static void WritePlantFields(StringBuilder json, Thing thing)
        {
            Plant plant = thing as Plant;
            if (plant == null)
            {
                return;
            }

            bool harvestable = SafeBool(delegate { return plant.HarvestableNow; });
            bool mature = SafeBool(delegate { return plant.def != null && plant.def.plant != null && plant.Growth >= plant.def.plant.harvestMinGrowth; });
            bool canCut = SafeBool(delegate {
                Designator_PlantsCut designator = new Designator_PlantsCut();
                AcceptanceReport report = designator.CanDesignateThing(plant);
                return report.Accepted;
            });
            bool canHarvest = SafeBool(delegate {
                Designator_PlantsHarvest designator = new Designator_PlantsHarvest();
                AcceptanceReport report = designator.CanDesignateThing(plant);
                return report.Accepted;
            });

            WriteFloat(json, "growth", plant.Growth, true);
            WriteBool(json, "mature", mature, true);
            WriteBool(json, "harvestableNow", harvestable, true);
            WriteBool(json, "canDesignateCut", canCut, true);
            WriteBool(json, "canDesignateHarvest", canHarvest, true);
        }

        private static string ThingType(Thing thing)
        {
            if (thing is Pawn)
            {
                Pawn pawn = (Pawn)thing;
                return pawn.Faction == Faction.OfPlayer ? "playerPawn" : (pawn.HostileTo(Faction.OfPlayer) ? "hostilePawn" : "pawn");
            }
            if (thing is Blueprint)
            {
                return "blueprint";
            }
            if (thing is Frame)
            {
                return "frame";
            }
            if (thing is Mineable)
            {
                return "mineable";
            }
            if (thing is Plant)
            {
                return "plant";
            }
            if (thing is Building)
            {
                return "building";
            }
            if (thing.def != null && thing.def.EverHaulable)
            {
                return "item";
            }
            return "thing";
        }

        private static bool IsRegionRelevantThing(Thing thing)
        {
            if (thing is Pawn)
            {
                Pawn pawn = (Pawn)thing;
                return pawn.Faction == Faction.OfPlayer || pawn.HostileTo(Faction.OfPlayer);
            }

            if (thing is Building || thing is Blueprint || thing is Frame || thing is Mineable)
            {
                return true;
            }

            if (thing is Plant)
            {
                Plant plant = (Plant)thing;
                return plant.def != null && (plant.def.plant == null || plant.def.plant.IsTree || plant.HarvestableNow);
            }

            return thing.def != null && (thing.def.EverHaulable || thing.def.defName == "Fire");
        }

        private static bool IsRelevantBuildingStateThing(Thing thing)
        {
            return thing is Building || thing is Blueprint || thing is Frame;
        }

        private static IntVec3 ColonyCenter(Map map)
        {
            List<Pawn> colonists = map.mapPawns.FreeColonistsSpawned;
            if (colonists.Count == 0)
            {
                return new IntVec3(map.Size.x / 2, 0, map.Size.z / 2);
            }

            int x = 0;
            int z = 0;
            for (int i = 0; i < colonists.Count; i++)
            {
                x += colonists[i].Position.x;
                z += colonists[i].Position.z;
            }

            return new IntVec3(x / colonists.Count, 0, z / colonists.Count);
        }

        private static bool TryAreaBounds(Map map, out CellBounds bounds)
        {
            bounds = new CellBounds();
            bool found = false;
            Area home = map.areaManager != null ? map.areaManager.Home : null;
            if (home == null)
            {
                return false;
            }

            for (int x = 0; x < map.Size.x; x++)
            {
                for (int z = 0; z < map.Size.z; z++)
                {
                    IntVec3 cell = new IntVec3(x, 0, z);
                    if (!home[cell])
                    {
                        continue;
                    }

                    bounds.Include(cell, ref found);
                }
            }

            return found;
        }

        private static bool TryZoneBounds(Zone zone, out CellBounds bounds)
        {
            bounds = new CellBounds();
            bool found = false;
            foreach (IntVec3 cell in zone.Cells)
            {
                bounds.Include(cell, ref found);
            }

            return found;
        }

        private static bool ZoneTouches(Zone zone, int minX, int minZ, int maxX, int maxZ)
        {
            foreach (IntVec3 cell in zone.Cells)
            {
                if (cell.x >= minX && cell.x <= maxX && cell.z >= minZ && cell.z <= maxZ)
                {
                    return true;
                }
            }

            return false;
        }

        public static string ZoneId(Zone zone)
        {
            return "zone-" + zone.ID;
        }

        private static string ZoneType(Zone zone)
        {
            if (zone is Zone_Growing)
            {
                return "growing";
            }
            if (zone is Zone_Stockpile)
            {
                Zone_Stockpile stockpile = (Zone_Stockpile)zone;
                return stockpile.GetInspectString().IndexOf("corpse", StringComparison.OrdinalIgnoreCase) >= 0 ? "dumping" : "stockpile";
            }

            return zone.GetType().Name;
        }

        private static bool? PoweredState(Thing thing)
        {
            CompPowerTrader comp = thing.TryGetComp<CompPowerTrader>();
            if (comp == null)
            {
                return null;
            }

            return comp.PowerOn;
        }

        private static bool IsVisible(Thing thing, Map map)
        {
            return thing != null && thing.Spawned && thing.Map == map && !thing.Position.Fogged(map);
        }

        private static string SafeThingId(Thing thing)
        {
            return thing != null ? thing.ThingID : null;
        }

        private static string SafeLabel(Thing thing)
        {
            return thing != null ? thing.LabelShortCap : null;
        }

        private static string SafeString(Func<string> getter)
        {
            try
            {
                return getter();
            }
            catch
            {
                return null;
            }
        }

        private static float SafeFloat(Func<float> getter)
        {
            try
            {
                return getter();
            }
            catch
            {
                return 0f;
            }
        }

        private static bool SafeBool(Func<bool> getter)
        {
            try
            {
                return getter();
            }
            catch
            {
                return false;
            }
        }

        private static Vector2 LongLat(Map map)
        {
            try
            {
                return Find.WorldGrid != null ? Find.WorldGrid.LongLatOf(map.Tile) : Vector2.zero;
            }
            catch
            {
                return Vector2.zero;
            }
        }

        private static void WriteBounds(StringBuilder json, CellBounds bounds)
        {
            json.Append("{");
            WriteInt(json, "minX", bounds.MinX, false);
            WriteInt(json, "minZ", bounds.MinZ, true);
            WriteInt(json, "maxX", bounds.MaxX, true);
            WriteInt(json, "maxZ", bounds.MaxZ, true);
            json.Append("}");
        }

        private static void WritePosition(StringBuilder json, string name, IntVec3 position, bool comma)
        {
            WriteName(json, name, comma);
            json.Append("{");
            WriteInt(json, "x", position.x, false);
            WriteInt(json, "z", position.z, true);
            json.Append("}");
        }

        private static void WriteSize(StringBuilder json, ThingDef def, Rot4 rotation, bool comma)
        {
            WriteName(json, "size", comma);
            if (def == null)
            {
                json.Append("null");
                return;
            }

            IntVec2 size = def.size;
            if (rotation.IsHorizontal)
            {
                size = new IntVec2(size.z, size.x);
            }

            json.Append("{");
            WriteInt(json, "x", size.x, false);
            WriteInt(json, "z", size.z, true);
            json.Append("}");
        }

        private static void WriteString(StringBuilder json, string name, string value, bool comma)
        {
            WriteName(json, name, comma);
            WriteStringValue(json, value);
        }

        private static void WriteStringValue(StringBuilder json, string value)
        {
            if (value == null)
            {
                json.Append("null");
            }
            else
            {
                json.Append("\"").Append(RimGPTJson.Escape(value)).Append("\"");
            }
        }

        private static void WriteInt(StringBuilder json, string name, int value, bool comma)
        {
            WriteName(json, name, comma);
            json.Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static void WriteFloat(StringBuilder json, string name, float value, bool comma)
        {
            WriteName(json, name, comma);
            json.Append(value.ToString("0.###", CultureInfo.InvariantCulture));
        }

        private static void WriteBool(StringBuilder json, string name, bool value, bool comma)
        {
            WriteName(json, name, comma);
            json.Append(value ? "true" : "false");
        }

        private static void WriteNullableBool(StringBuilder json, string name, bool? value, bool comma)
        {
            WriteName(json, name, comma);
            if (value.HasValue)
            {
                json.Append(value.Value ? "true" : "false");
            }
            else
            {
                json.Append("null");
            }
        }

        private static void WriteName(StringBuilder json, string name, bool comma)
        {
            if (comma)
            {
                json.Append(",");
            }

            json.Append("\"").Append(name).Append("\":");
        }

        public struct CellBounds
        {
            public int MinX;
            public int MinZ;
            public int MaxX;
            public int MaxZ;

            public void Include(IntVec3 cell, ref bool found)
            {
                if (!found)
                {
                    MinX = MaxX = cell.x;
                    MinZ = MaxZ = cell.z;
                    found = true;
                    return;
                }

                MinX = Math.Min(MinX, cell.x);
                MinZ = Math.Min(MinZ, cell.z);
                MaxX = Math.Max(MaxX, cell.x);
                MaxZ = Math.Max(MaxZ, cell.z);
            }
        }
    }
}
