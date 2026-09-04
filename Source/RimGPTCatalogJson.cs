using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using RimWorld;
using Verse;

namespace RimGPT
{
    public static class RimGPTCatalogJson
    {
        private const int MaxBuildOptions = 80;
        private const int MaxGrowablePlants = 120;

        public static string BuildBuildOptionsJson(string category, string search)
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return "{\"gameLoaded\":false,\"options\":[]}";
            }

            StringBuilder json = new StringBuilder(16384);
            json.Append("{\"schemaVersion\":2,\"gameLoaded\":true,\"options\":[");
            int written = 0;
            WriteBuildableList(json, DefDatabase<ThingDef>.AllDefsListForReading, category, search, ref written);
            WriteBuildableList(json, DefDatabase<TerrainDef>.AllDefsListForReading, category, search, ref written);
            json.Append("]}");
            return json.ToString();
        }

        public static string BuildBuildInfoJson(string defName)
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return "{\"gameLoaded\":false}";
            }

            BuildableDef buildable = FindBuildable(defName);
            if (buildable == null)
            {
                return "{\"error\":\"unknownBuildDef\"}";
            }

            StringBuilder json = new StringBuilder(4096);
            json.Append("{\"schemaVersion\":2,\"gameLoaded\":true,\"buildable\":");
            WriteBuildOption(json, buildable);
            json.Append("}");
            return json.ToString();
        }

        public static string BuildGrowablePlantsJson()
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return "{\"gameLoaded\":false,\"plants\":[]}";
            }

            Map map = Find.CurrentMap;
            StringBuilder json = new StringBuilder(16384);
            json.Append("{\"schemaVersion\":2,\"gameLoaded\":true,\"plants\":[");
            int written = 0;
            List<ThingDef> defs = DefDatabase<ThingDef>.AllDefsListForReading;
            for (int i = 0; i < defs.Count && written < MaxGrowablePlants; i++)
            {
                ThingDef def = defs[i];
                if (def == null || def.plant == null || !def.plant.Sowable)
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                json.Append("{");
                WriteString(json, "defName", def.defName, false);
                WriteString(json, "label", def.label, true);
                WriteFloat(json, "fertilityMin", def.plant.fertilityMin, true);
                WriteInt(json, "sowMinSkill", def.plant.sowMinSkill, true);
                WriteBool(json, "growthSeasonNow", SafeBool(delegate { return PlantUtility.GrowthSeasonNow(map, def); }), true);
                WriteBool(json, "sowable", true, true);
                json.Append("}");
                written++;
            }
            json.Append("]}");
            return json.ToString();
        }

        public static string BuildCheckBuildPlacementsJson(List<RimGPTBlueprintPlacement> placements)
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return "{\"gameLoaded\":false,\"placements\":[]}";
            }

            StringBuilder json = new StringBuilder(8192);
            json.Append("{\"schemaVersion\":2,\"gameLoaded\":true,\"placements\":[");
            if (placements != null)
            {
                int count = Math.Min(placements.Count, 100);
                for (int i = 0; i < count; i++)
                {
                    PlacementCheckResult result = CheckPlacement(placements[i]);
                    if (i > 0)
                    {
                        json.Append(",");
                    }

                    json.Append("{");
                    WriteInt(json, "index", i, false);
                    WriteBool(json, "valid", result.Valid, true);
                    WriteString(json, "reason", result.Reason, true);
                    WriteString(json, "requiredTerrainAffordance", result.RequiredTerrainAffordance, true);
                    json.Append("}");
                }
            }
            json.Append("]}");
            return json.ToString();
        }

        public static BuildableDef FindBuildable(string defName)
        {
            if (string.IsNullOrEmpty(defName))
            {
                return null;
            }

            ThingDef thingDef = DefDatabase<ThingDef>.GetNamedSilentFail(defName);
            if (thingDef != null)
            {
                return thingDef;
            }

            TerrainDef terrainDef = DefDatabase<TerrainDef>.GetNamedSilentFail(defName);
            if (terrainDef != null)
            {
                return terrainDef;
            }

            return null;
        }

        public static bool PlayerCanBuild(BuildableDef buildable)
        {
            return buildable != null && BuildCopyCommandUtility.FindAllowedDesignator(buildable, true) != null;
        }

        private static void WriteBuildableList<T>(StringBuilder json, List<T> defs, string category, string search, ref int written) where T : BuildableDef
        {
            for (int i = 0; i < defs.Count && written < MaxBuildOptions; i++)
            {
                BuildableDef buildable = defs[i];
                if (!ShouldIncludeBuildable(buildable, category, search))
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteBuildOption(json, buildable);
                written++;
            }
        }

        private static bool ShouldIncludeBuildable(BuildableDef buildable, string category, string search)
        {
            if (buildable == null || buildable.designationCategory == null)
            {
                return false;
            }

            if (!string.IsNullOrEmpty(category) && !Matches(buildable.designationCategory.defName, category) && !Matches(buildable.designationCategory.label, category))
            {
                return false;
            }

            if (!string.IsNullOrEmpty(search) && !Matches(buildable.defName, search) && !Matches(buildable.label, search))
            {
                return false;
            }

            return PlayerCanBuild(buildable);
        }

        private static void WriteBuildOption(StringBuilder json, BuildableDef buildable)
        {
            ThingDef thingDef = buildable as ThingDef;
            ThingDef stuff = thingDef != null && thingDef.MadeFromStuff ? GenStuff.DefaultStuffFor(buildable) : null;
            bool researchSatisfied = buildable.IsResearchFinished;
            bool available = PlayerCanBuild(buildable);

            json.Append("{");
            WriteString(json, "defName", buildable.defName, false);
            WriteString(json, "label", buildable.label, true);
            WriteString(json, "category", buildable.designationCategory != null ? buildable.designationCategory.defName : null, true);
            WriteName(json, "size", true);
            IntVec2 size = thingDef != null ? thingDef.size : IntVec2.One;
            json.Append("{");
            WriteInt(json, "x", size.x, false);
            WriteInt(json, "z", size.z, true);
            json.Append("}");
            WriteBool(json, "stuffable", thingDef != null && thingDef.MadeFromStuff, true);
            WriteBool(json, "available", available, true);
            WriteBool(json, "researchSatisfied", researchSatisfied, true);
            WriteString(json, "requiredTerrainAffordance", RequiredTerrainAffordance(buildable, stuff), true);
            WriteFootprint(json, thingDef, true);
            WriteCost(json, buildable, stuff, true);
            json.Append("}");
        }

        private static PlacementCheckResult CheckPlacement(RimGPTBlueprintPlacement placement)
        {
            if (placement == null)
            {
                return PlacementCheckResult.Failed("Missing placement", null);
            }

            Map map = Find.CurrentMap;
            IntVec3 cell = new IntVec3(placement.X, 0, placement.Z);
            if (!cell.InBounds(map))
            {
                return PlacementCheckResult.Failed("Cell is outside the current map", null);
            }

            if (cell.Fogged(map))
            {
                return PlacementCheckResult.Failed("Cell is not currently visible", null);
            }

            BuildableDef buildable = FindBuildable(placement.BuildDef);
            if (buildable == null)
            {
                return PlacementCheckResult.Failed("Unknown buildDef", null);
            }

            if (!PlayerCanBuild(buildable))
            {
                return PlacementCheckResult.Failed("Build option is not currently available to the player", null);
            }

            Rot4 rotation;
            if (!TryParseRotation(placement.Rotation, out rotation))
            {
                return PlacementCheckResult.Failed("Unknown rotation", null);
            }

            string stuffError;
            ThingDef stuff = ResolveStuff(buildable, placement.StuffDef, out stuffError);
            string required = RequiredTerrainAffordance(buildable, stuff);
            if (stuffError != null)
            {
                return PlacementCheckResult.Failed(stuffError, required);
            }

            AcceptanceReport report = GenConstruct.CanPlaceBlueprintAt(buildable, cell, rotation, map, false, null, null, stuff, false, false, false);
            if (!report)
            {
                return PlacementCheckResult.Failed(SafeReportReason(report, "Blueprint cannot be placed at this cell"), required);
            }

            return PlacementCheckResult.Succeeded(required);
        }

        private static string RequiredTerrainAffordance(BuildableDef buildable, ThingDef stuff)
        {
            TerrainAffordanceDef affordance = null;
            try
            {
                affordance = ThingUtility.GetTerrainAffordanceNeed(buildable, stuff);
            }
            catch
            {
                affordance = buildable != null ? buildable.terrainAffordanceNeeded : null;
            }

            return affordance != null ? affordance.defName : null;
        }

        private static void WriteFootprint(StringBuilder json, ThingDef thingDef, bool comma)
        {
            WriteName(json, "footprint", comma);
            if (thingDef == null)
            {
                json.Append("null");
                return;
            }

            json.Append("{\"northSouth\":{");
            WriteInt(json, "x", thingDef.size.x, false);
            WriteInt(json, "z", thingDef.size.z, true);
            json.Append("},\"eastWest\":{");
            WriteInt(json, "x", thingDef.size.z, false);
            WriteInt(json, "z", thingDef.size.x, true);
            json.Append("}}");
        }

        private static bool TryParseRotation(string rotationName, out Rot4 rotation)
        {
            string value = string.IsNullOrEmpty(rotationName) ? "North" : rotationName;
            if (value.Equals("North", StringComparison.OrdinalIgnoreCase))
            {
                rotation = Rot4.North;
                return true;
            }
            if (value.Equals("East", StringComparison.OrdinalIgnoreCase))
            {
                rotation = Rot4.East;
                return true;
            }
            if (value.Equals("South", StringComparison.OrdinalIgnoreCase))
            {
                rotation = Rot4.South;
                return true;
            }
            if (value.Equals("West", StringComparison.OrdinalIgnoreCase))
            {
                rotation = Rot4.West;
                return true;
            }

            rotation = Rot4.North;
            return false;
        }

        private static ThingDef ResolveStuff(BuildableDef buildable, string stuffDefName, out string error)
        {
            error = null;
            ThingDef thingDef = buildable as ThingDef;
            if (thingDef == null || !thingDef.MadeFromStuff)
            {
                if (!string.IsNullOrEmpty(stuffDefName))
                {
                    error = "This buildDef does not use stuffDef";
                }

                return null;
            }

            ThingDef stuff = null;
            if (!string.IsNullOrEmpty(stuffDefName))
            {
                stuff = DefDatabase<ThingDef>.GetNamedSilentFail(stuffDefName);
                if (stuff == null)
                {
                    error = "Unknown stuffDef";
                    return null;
                }
            }
            else
            {
                stuff = GenStuff.DefaultStuffFor(buildable);
            }

            if (stuff == null)
            {
                error = "No valid stuffDef is available for this buildDef";
                return null;
            }

            bool allowed = false;
            foreach (ThingDef allowedStuff in GenStuff.AllowedStuffsFor(buildable, TechLevel.Undefined, false))
            {
                if (allowedStuff == stuff)
                {
                    allowed = true;
                    break;
                }
            }

            if (!allowed)
            {
                error = "stuffDef is not valid for this buildDef";
                return null;
            }

            return stuff;
        }

        private static string SafeReportReason(AcceptanceReport report, string fallback)
        {
            string reason = report.Reason;
            return string.IsNullOrEmpty(reason) ? fallback : reason;
        }

        private static void WriteCost(StringBuilder json, BuildableDef buildable, ThingDef stuff, bool comma)
        {
            WriteName(json, "cost", comma);
            json.Append("[");
            List<ThingDefCountClass> costs = null;
            try
            {
                costs = CostListCalculator.CostListAdjusted(buildable, stuff, false);
            }
            catch
            {
                costs = buildable.costList;
            }

            if (costs != null)
            {
                for (int i = 0; i < costs.Count; i++)
                {
                    if (i > 0)
                    {
                        json.Append(",");
                    }

                    ThingDefCountClass cost = costs[i];
                    json.Append("{");
                    WriteString(json, "defName", cost.thingDef != null ? cost.thingDef.defName : null, false);
                    WriteString(json, "label", cost.thingDef != null ? cost.thingDef.label : null, true);
                    WriteInt(json, "count", cost.count, true);
                    json.Append("}");
                }
            }

            json.Append("]");
        }

        private static bool Matches(string value, string needle)
        {
            return value != null && needle != null && value.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0;
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

        private sealed class PlacementCheckResult
        {
            public bool Valid;
            public string Reason;
            public string RequiredTerrainAffordance;

            public static PlacementCheckResult Succeeded(string requiredTerrainAffordance)
            {
                return new PlacementCheckResult
                {
                    Valid = true,
                    Reason = "ok",
                    RequiredTerrainAffordance = requiredTerrainAffordance
                };
            }

            public static PlacementCheckResult Failed(string reason, string requiredTerrainAffordance)
            {
                return new PlacementCheckResult
                {
                    Valid = false,
                    Reason = reason,
                    RequiredTerrainAffordance = requiredTerrainAffordance
                };
            }
        }

        private static void WriteString(StringBuilder json, string name, string value, bool comma)
        {
            WriteName(json, name, comma);
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

        private static void WriteName(StringBuilder json, string name, bool comma)
        {
            if (comma)
            {
                json.Append(",");
            }

            json.Append("\"").Append(name).Append("\":");
        }
    }
}
