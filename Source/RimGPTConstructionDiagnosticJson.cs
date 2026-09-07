using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using RimWorld;
using Verse;

namespace RimGPT
{
    public static class RimGPTConstructionDiagnosticJson
    {
        public static string Build(string thingId)
        {
            Map map = Find.CurrentMap;
            if (map == null || Current.Game == null)
            {
                return Unavailable(false);
            }

            Thing target = FindVisiblePlayerTarget(map, thingId);
            IConstructible constructible = target as IConstructible;
            if (target == null || constructible == null)
            {
                return Unavailable(true);
            }

            List<ThingDefCountClass> costs;
            try
            {
                costs = constructible.TotalMaterialCost();
            }
            catch (Exception ex)
            {
                Log.Warning("[RimGPT] Could not read construction costs for " + target.ThingID + ": " + ex.Message);
                return BuildKnownTarget(target, constructible, null, "unknown");
            }

            return BuildKnownTarget(target, constructible, CombineCosts(costs), null);
        }

        private static string BuildKnownTarget(
            Thing target,
            IConstructible constructible,
            Dictionary<ThingDef, int> costs,
            string forcedBlocker)
        {
            Map map = target.Map;
            Dictionary<ThingDef, ResourceCounts> resources = CountVisibleResources(map, costs);
            List<Requirement> requirements = new List<Requirement>();
            bool missingMaterials = false;
            if (costs != null)
            {
                foreach (KeyValuePair<ThingDef, int> pair in costs)
                {
                    int remaining;
                    try
                    {
                        remaining = Math.Max(0, constructible.ThingCountNeeded(pair.Key));
                    }
                    catch
                    {
                        return BuildKnownTarget(target, constructible, null, "unknown");
                    }

                    ResourceCounts count;
                    if (!resources.TryGetValue(pair.Key, out count))
                    {
                        count = new ResourceCounts();
                    }
                    int missing = Math.Max(0, remaining - count.Available);
                    missingMaterials = missingMaterials || missing > 0;
                    requirements.Add(new Requirement
                    {
                        Def = pair.Key,
                        Required = pair.Value,
                        Remaining = remaining,
                        Available = count.Available,
                        Forbidden = count.Forbidden,
                        Missing = missing
                    });
                }
                requirements.Sort(delegate(Requirement a, Requirement b)
                {
                    return string.CompareOrdinal(a.Def.defName, b.Def.defName);
                });
            }

            LaborCounts labor = CountConstructionLabor(map);
            List<string> blockers = new List<string>();
            if (!string.IsNullOrEmpty(forcedBlocker))
            {
                blockers.Add(forcedBlocker);
            }
            else
            {
                if (missingMaterials) blockers.Add("missingMaterials");
                if (labor.Capable == 0) blockers.Add("noCapableConstructor");
                else if (labor.Enabled == 0) blockers.Add("constructionDisabled");
            }

            BuildableDef buildable = null;
            ThingDef stuff = null;
            try
            {
                Blueprint blueprint = target as Blueprint;
                Frame frame = target as Frame;
                buildable = blueprint != null ? blueprint.EntityToBuild() : (frame != null ? frame.BuildDef : null);
                stuff = constructible.EntityToBuildStuff();
            }
            catch
            {
                if (blockers.Count == 0) blockers.Add("unknown");
            }

            StringBuilder json = new StringBuilder(768);
            json.Append("{\"gameLoaded\":true,\"targetAvailable\":true,\"target\":{");
            WriteString(json, "id", target.ThingID, false);
            WriteString(json, "stage", target is Frame ? "frame" : "blueprint", true);
            WriteString(json, "defName", buildable != null ? buildable.defName : null, true);
            WriteString(json, "label", SafeLabel(buildable), true);
            WritePosition(json, target.Position, true);
            WriteString(json, "stuffDef", stuff != null ? stuff.defName : null, true);
            json.Append("},\"requirements\":[");
            for (int i = 0; i < requirements.Count; i++)
            {
                if (i > 0) json.Append(",");
                WriteRequirement(json, requirements[i]);
            }
            json.Append("],\"labor\":{");
            WriteInt(json, "capable", labor.Capable, false);
            WriteInt(json, "enabled", labor.Enabled, true);
            WriteInt(json, "idleEnabled", labor.IdleEnabled, true);
            json.Append("},\"blockers\":[");
            for (int i = 0; i < blockers.Count; i++)
            {
                if (i > 0) json.Append(",");
                WriteStringValue(json, blockers[i]);
            }
            json.Append("],");
            WriteString(json, "primaryBlocker", blockers.Count > 0 ? blockers[0] : "none", false);
            json.Append("}");
            return json.ToString();
        }

        private static Thing FindVisiblePlayerTarget(Map map, string thingId)
        {
            if (map.listerThings == null || string.IsNullOrEmpty(thingId))
            {
                return null;
            }
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count; i++)
            {
                Thing thing = things[i];
                if (thing == null || thing.ThingID != thingId || !thing.Spawned || thing.Map != map)
                {
                    continue;
                }
                if (!(thing is Blueprint) && !(thing is Frame))
                {
                    return null;
                }
                if (thing.Faction != Faction.OfPlayer || thing.Position.Fogged(map))
                {
                    return null;
                }
                return thing;
            }
            return null;
        }

        private static Dictionary<ThingDef, int> CombineCosts(List<ThingDefCountClass> costs)
        {
            Dictionary<ThingDef, int> result = new Dictionary<ThingDef, int>();
            if (costs == null)
            {
                return result;
            }
            for (int i = 0; i < costs.Count; i++)
            {
                ThingDefCountClass cost = costs[i];
                if (cost == null || cost.thingDef == null || cost.count <= 0)
                {
                    continue;
                }
                int current;
                result.TryGetValue(cost.thingDef, out current);
                result[cost.thingDef] = current + cost.count;
            }
            return result;
        }

        private static Dictionary<ThingDef, ResourceCounts> CountVisibleResources(
            Map map,
            Dictionary<ThingDef, int> costs)
        {
            Dictionary<ThingDef, ResourceCounts> result = new Dictionary<ThingDef, ResourceCounts>();
            if (costs == null || costs.Count == 0 || map.listerThings == null)
            {
                return result;
            }
            List<Thing> haulables = map.listerThings.ThingsInGroup(ThingRequestGroup.HaulableEver);
            for (int i = 0; i < haulables.Count; i++)
            {
                Thing thing = haulables[i];
                if (thing == null || thing.def == null || !costs.ContainsKey(thing.def)
                    || !thing.Spawned || thing.Map != map || thing.Position.Fogged(map))
                {
                    continue;
                }
                ResourceCounts count;
                if (!result.TryGetValue(thing.def, out count))
                {
                    count = new ResourceCounts();
                    result[thing.def] = count;
                }
                if (thing.IsForbidden(Faction.OfPlayer)) count.Forbidden += thing.stackCount;
                else count.Available += thing.stackCount;
            }
            return result;
        }

        private static LaborCounts CountConstructionLabor(Map map)
        {
            LaborCounts result = new LaborCounts();
            WorkTypeDef construction = WorkTypeDefOf.Construction;
            if (construction == null || map.mapPawns == null)
            {
                return result;
            }
            List<Pawn> colonists = map.mapPawns.FreeColonistsSpawned;
            for (int i = 0; i < colonists.Count; i++)
            {
                Pawn pawn = colonists[i];
                if (pawn == null || pawn.Dead || pawn.Downed || pawn.WorkTypeIsDisabled(construction))
                {
                    continue;
                }
                result.Capable++;
                if (pawn.workSettings == null)
                {
                    continue;
                }
                pawn.workSettings.EnableAndInitializeIfNotAlreadyInitialized();
                if (pawn.workSettings.GetPriority(construction) <= 0)
                {
                    continue;
                }
                result.Enabled++;
                if (!pawn.Drafted && pawn.mindState != null && pawn.mindState.IsIdle)
                {
                    result.IdleEnabled++;
                }
            }
            return result;
        }

        private static string Unavailable(bool gameLoaded)
        {
            return "{\"gameLoaded\":" + (gameLoaded ? "true" : "false")
                + ",\"targetAvailable\":false,\"requirements\":[],\"primaryBlocker\":\"unavailableOrInvalidTarget\"}";
        }

        private static string SafeLabel(Def def)
        {
            try { return def != null ? def.LabelCap : null; }
            catch { return def != null ? def.defName : null; }
        }

        private static void WriteRequirement(StringBuilder json, Requirement value)
        {
            json.Append("{");
            WriteString(json, "defName", value.Def.defName, false);
            WriteString(json, "label", SafeLabel(value.Def), true);
            WriteInt(json, "required", value.Required, true);
            WriteInt(json, "remaining", value.Remaining, true);
            WriteInt(json, "delivered", Math.Max(0, value.Required - value.Remaining), true);
            WriteInt(json, "available", value.Available, true);
            WriteInt(json, "forbidden", value.Forbidden, true);
            WriteInt(json, "missing", value.Missing, true);
            json.Append("}");
        }

        private static void WritePosition(StringBuilder json, IntVec3 position, bool comma)
        {
            if (comma) json.Append(",");
            json.Append("\"position\":{");
            WriteInt(json, "x", position.x, false);
            WriteInt(json, "z", position.z, true);
            json.Append("}");
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
            json.Append("\"").Append(name).Append("\":")
                .Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private sealed class ResourceCounts
        {
            public int Available;
            public int Forbidden;
        }

        private sealed class LaborCounts
        {
            public int Capable;
            public int Enabled;
            public int IdleEnabled;
        }

        private sealed class Requirement
        {
            public ThingDef Def;
            public int Required;
            public int Remaining;
            public int Available;
            public int Forbidden;
            public int Missing;
        }
    }
}
