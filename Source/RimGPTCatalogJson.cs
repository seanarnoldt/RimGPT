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
            WriteCost(json, buildable, stuff, true);
            json.Append("}");
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
