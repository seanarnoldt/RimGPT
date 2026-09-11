using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using RimWorld;
using Verse;

namespace RimGPT
{
    public static class RimGPTResourceSourceJson
    {
        private const int MaxSourceGroups = 8;
        private const int MaxSamples = 16;
        private const int MaxSamplesPerGroup = 6;

        public static string Build(string resourceDefName)
        {
            Map map = Find.CurrentMap;
            if (map == null || Current.Game == null)
            {
                return "{\"gameLoaded\":false,\"resourceKnown\":false,\"supported\":false,\"sources\":[],\"sourceTypes\":[]}";
            }

            ThingDef resourceDef = DefDatabase<ThingDef>.GetNamedSilentFail(resourceDefName);
            if (resourceDef == null || resourceDef.category != ThingCategory.Item)
            {
                return UnknownResource(resourceDefName);
            }

            ResourceResult result = new ResourceResult(resourceDef);
            CountVisibleHaulables(map, result);
            if (resourceDef.defName == "WoodLog")
            {
                FindCuttablePlants(map, result);
            }
            else if (resourceDef.defName == "Steel")
            {
                FindMineableDeposits(map, result);
            }

            return WriteResult(result);
        }

        private static void CountVisibleHaulables(Map map, ResourceResult result)
        {
            if (map.listerThings == null)
            {
                return;
            }

            SourceGroup group = new SourceGroup("haulableItem", result.ResourceDef);
            List<Thing> haulables = map.listerThings.ThingsInGroup(ThingRequestGroup.HaulableEver);
            for (int i = 0; i < haulables.Count; i++)
            {
                Thing thing = haulables[i];
                if (!IsVisibleOnMap(thing, map) || thing.def != result.ResourceDef)
                {
                    continue;
                }

                bool forbidden = thing.IsForbidden(Faction.OfPlayer);
                if (forbidden) result.Forbidden += thing.stackCount;
                else result.Available += thing.stackCount;
                group.Count++;
                group.Quantity += thing.stackCount;
                group.Candidates.Add(new SourceSample(thing, thing.stackCount, forbidden));
            }

            if (group.Count > 0)
            {
                result.Groups.Add(group);
            }
        }

        private static void FindCuttablePlants(Map map, ResourceResult result)
        {
            if (map.listerThings == null)
            {
                return;
            }

            Dictionary<ThingDef, SourceGroup> byDef = new Dictionary<ThingDef, SourceGroup>();
            Designator_PlantsCut designator = new Designator_PlantsCut();
            List<Thing> plants = map.listerThings.ThingsInGroup(ThingRequestGroup.Plant);
            for (int i = 0; i < plants.Count; i++)
            {
                Plant plant = plants[i] as Plant;
                if (!IsVisibleOnMap(plant, map) || plant.def == null || plant.def.plant == null
                    || plant.def.plant.choppedThingDef != result.ResourceDef || plant.YieldNow() <= 0)
                {
                    continue;
                }

                bool alreadyDesignated = map.designationManager != null
                    && map.designationManager.DesignationOn(plant, DesignationDefOf.CutPlant) != null;
                bool canDesignate = false;
                if (!alreadyDesignated)
                {
                    try
                    {
                        canDesignate = designator.CanDesignateThing(plant).Accepted;
                    }
                    catch
                    {
                        canDesignate = false;
                    }
                }
                if (!alreadyDesignated && !canDesignate)
                {
                    continue;
                }

                SourceGroup group;
                if (!byDef.TryGetValue(plant.def, out group))
                {
                    group = new SourceGroup("cuttablePlant", plant.def);
                    byDef.Add(plant.def, group);
                }
                group.Count++;
                if (alreadyDesignated) group.AlreadyDesignated++;
                group.Candidates.Add(new SourceSample(plant, 0, false));
            }

            AddSortedGroups(result.Groups, byDef);
        }

        private static void FindMineableDeposits(Map map, ResourceResult result)
        {
            if (map.listerThings == null)
            {
                return;
            }

            Dictionary<ThingDef, SourceGroup> byDef = new Dictionary<ThingDef, SourceGroup>();
            Designator_Mine designator = new Designator_Mine();
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count; i++)
            {
                Mineable mineable = things[i] as Mineable;
                if (!IsVisibleOnMap(mineable, map) || mineable.def == null || mineable.def.building == null
                    || mineable.def.building.mineableThing != result.ResourceDef)
                {
                    continue;
                }

                bool alreadyDesignated = map.designationManager != null
                    && map.designationManager.DesignationOn(mineable, DesignationDefOf.Mine) != null;
                bool canDesignate = false;
                if (!alreadyDesignated)
                {
                    try
                    {
                        canDesignate = designator.CanDesignateThing(mineable).Accepted;
                    }
                    catch
                    {
                        canDesignate = false;
                    }
                }
                if (!alreadyDesignated && !canDesignate)
                {
                    continue;
                }

                SourceGroup group;
                if (!byDef.TryGetValue(mineable.def, out group))
                {
                    group = new SourceGroup("mineableDeposit", mineable.def);
                    byDef.Add(mineable.def, group);
                }
                group.Count++;
                group.Quantity += Math.Max(0, mineable.def.building.mineableYield);
                if (alreadyDesignated) group.AlreadyDesignated++;
                group.Candidates.Add(new SourceSample(mineable, 0, false));
            }

            AddSortedGroups(result.Groups, byDef);
        }

        private static void AddSortedGroups(List<SourceGroup> destination, Dictionary<ThingDef, SourceGroup> groups)
        {
            List<SourceGroup> sorted = new List<SourceGroup>(groups.Values);
            sorted.Sort(delegate(SourceGroup a, SourceGroup b)
            {
                return string.CompareOrdinal(a.Def.defName, b.Def.defName);
            });
            destination.AddRange(sorted);
        }

        private static bool IsVisibleOnMap(Thing thing, Map map)
        {
            return thing != null && thing.Spawned && thing.Map == map && thing.def != null
                && !thing.Position.Fogged(map);
        }

        private static string WriteResult(ResourceResult result)
        {
            StringBuilder json = new StringBuilder(1024);
            json.Append("{\"gameLoaded\":true,\"resourceKnown\":true,\"supported\":true,");
            WriteString(json, "resourceDef", result.ResourceDef.defName, false);
            WriteString(json, "label", SafeLabel(result.ResourceDef), true);
            WriteInt(json, "available", result.Available, true);
            WriteInt(json, "forbidden", result.Forbidden, true);
            json.Append(",\"sources\":[");

            int groupLimit = Math.Min(MaxSourceGroups, result.Groups.Count);
            int samplesRemaining = MaxSamples;
            bool truncated = result.Groups.Count > groupLimit;
            List<string> sourceTypes = new List<string>();
            for (int i = 0; i < groupLimit; i++)
            {
                SourceGroup group = result.Groups[i];
                if (i > 0) json.Append(",");
                if (!sourceTypes.Contains(group.Type)) sourceTypes.Add(group.Type);
                truncated = WriteGroup(json, group, ref samplesRemaining) || truncated;
            }
            json.Append("],\"sourceTypes\":[");
            for (int i = 0; i < sourceTypes.Count; i++)
            {
                if (i > 0) json.Append(",");
                WriteStringValue(json, sourceTypes[i]);
            }
            json.Append("],");
            WriteBool(json, "truncated", truncated, false);
            json.Append("}");
            return json.ToString();
        }

        private static bool WriteGroup(StringBuilder json, SourceGroup group, ref int samplesRemaining)
        {
            group.Candidates.Sort(delegate(SourceSample a, SourceSample b)
            {
                int z = a.Z.CompareTo(b.Z);
                if (z != 0) return z;
                int x = a.X.CompareTo(b.X);
                return x != 0 ? x : string.CompareOrdinal(a.Id, b.Id);
            });

            json.Append("{");
            WriteString(json, "type", group.Type, false);
            WriteString(json, "defName", group.Def.defName, true);
            WriteString(json, "label", SafeLabel(group.Def), true);
            WriteInt(json, "count", group.Count, true);
            if (group.Type == "haulableItem") WriteInt(json, "quantity", group.Quantity, true);
            if (group.Type == "mineableDeposit") WriteInt(json, "nominalYield", group.Quantity, true);
            if (group.AlreadyDesignated > 0) WriteInt(json, "alreadyDesignated", group.AlreadyDesignated, true);
            json.Append(",\"sample\":[");
            int sampleCount = Math.Min(Math.Min(MaxSamplesPerGroup, group.Candidates.Count), samplesRemaining);
            for (int i = 0; i < sampleCount; i++)
            {
                if (i > 0) json.Append(",");
                SourceSample sample = group.Candidates[i];
                json.Append("{");
                if (group.Type == "haulableItem") WriteString(json, "id", sample.Id, false);
                WriteInt(json, "x", sample.X, group.Type == "haulableItem");
                WriteInt(json, "z", sample.Z, true);
                if (group.Type == "haulableItem")
                {
                    WriteInt(json, "quantity", sample.Quantity, true);
                    WriteBool(json, "forbidden", sample.Forbidden, true);
                }
                json.Append("}");
            }
            samplesRemaining -= sampleCount;
            json.Append("]}");
            return sampleCount < group.Candidates.Count;
        }

        private static string UnknownResource(string resourceDefName)
        {
            StringBuilder json = new StringBuilder(192);
            json.Append("{\"gameLoaded\":true,\"resourceKnown\":false,\"supported\":false,");
            WriteString(json, "resourceDef", resourceDefName, false);
            json.Append(",\"available\":0,\"forbidden\":0,\"sources\":[],\"sourceTypes\":[],\"reason\":\"unknownOrUnsupportedResourceDef\"}");
            return json.ToString();
        }

        private static string SafeLabel(Def def)
        {
            try { return def != null ? def.LabelCap : null; }
            catch { return def != null ? def.defName : null; }
        }

        private static void WriteString(StringBuilder json, string name, string value, bool comma)
        {
            if (comma) json.Append(",");
            json.Append("\"").Append(name).Append("\":");
            WriteStringValue(json, value);
        }

        private static void WriteStringValue(StringBuilder json, string value)
        {
            if (value == null) json.Append("null");
            else json.Append("\"").Append(RimGPTJson.Escape(value)).Append("\"");
        }

        private static void WriteInt(StringBuilder json, string name, int value, bool comma)
        {
            if (comma) json.Append(",");
            json.Append("\"").Append(name).Append("\":").Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static void WriteBool(StringBuilder json, string name, bool value, bool comma)
        {
            if (comma) json.Append(",");
            json.Append("\"").Append(name).Append("\":").Append(value ? "true" : "false");
        }

        private sealed class ResourceResult
        {
            public readonly ThingDef ResourceDef;
            public readonly List<SourceGroup> Groups = new List<SourceGroup>();
            public int Available;
            public int Forbidden;

            public ResourceResult(ThingDef resourceDef)
            {
                ResourceDef = resourceDef;
            }
        }

        private sealed class SourceGroup
        {
            public readonly string Type;
            public readonly ThingDef Def;
            public readonly List<SourceSample> Candidates = new List<SourceSample>();
            public int Count;
            public int Quantity;
            public int AlreadyDesignated;

            public SourceGroup(string type, ThingDef def)
            {
                Type = type;
                Def = def;
            }
        }

        private sealed class SourceSample
        {
            public readonly string Id;
            public readonly int X;
            public readonly int Z;
            public readonly int Quantity;
            public readonly bool Forbidden;

            public SourceSample(Thing thing, int quantity, bool forbidden)
            {
                Id = thing.ThingID;
                X = thing.Position.x;
                Z = thing.Position.z;
                Quantity = quantity;
                Forbidden = forbidden;
            }
        }
    }
}
