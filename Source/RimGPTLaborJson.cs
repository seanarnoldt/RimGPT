using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using RimWorld;
using Verse;

namespace RimGPT
{
    public static class RimGPTLaborJson
    {
        private const int MaxIdleColonists = 20;
        private const int MaxWorkTypesPerPawn = 20;
        private const int MaxDesignationTypes = 24;

        public static string Build(Map map)
        {
            List<Pawn> idle = new List<Pawn>();
            List<Pawn> capableIdle = new List<Pawn>();
            Dictionary<string, int> enabledWorkers = new Dictionary<string, int>();
            List<Pawn> colonists = map.mapPawns.FreeColonistsSpawned;
            for (int i = 0; i < colonists.Count; i++)
            {
                Pawn pawn = colonists[i];
                if (pawn == null || pawn.Dead)
                {
                    continue;
                }

                bool hasEnabledWork = IndexEnabledWork(pawn, enabledWorkers);
                bool isIdle = pawn.mindState != null && pawn.mindState.IsIdle && !pawn.Drafted && !pawn.Downed;
                if (isIdle)
                {
                    idle.Add(pawn);
                    if (hasEnabledWork)
                    {
                        capableIdle.Add(pawn);
                    }
                }
            }

            int blueprints = 0;
            int frames = 0;
            int bills = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count; i++)
            {
                Thing thing = things[i];
                if (thing == null || !thing.Spawned || thing.Map != map || thing.Position.Fogged(map))
                {
                    continue;
                }
                if (thing is Blueprint)
                {
                    blueprints++;
                }
                else if (thing is Frame)
                {
                    frames++;
                }

                IBillGiver giver = thing as IBillGiver;
                if (giver != null && giver.BillStack != null && giver.BillStack.Bills != null)
                {
                    for (int b = 0; b < giver.BillStack.Bills.Count; b++)
                    {
                        Bill bill = giver.BillStack.Bills[b];
                        if (bill != null && !bill.suspended)
                        {
                            bills++;
                        }
                    }
                }
            }

            Dictionary<string, int> designations = CountDesignations(map);
            int haulables = SafeCount(delegate { return map.listerHaulables.ThingsPotentiallyNeedingHauling().Count; });

            StringBuilder json = new StringBuilder(4096);
            json.Append("{");
            WriteInt(json, "idleColonistCount", idle.Count, false);
            WriteInt(json, "capableIdleColonistCount", capableIdle.Count, true);
            WritePawns(json, "idleColonists", idle, true);
            WritePawns(json, "capableIdleColonists", capableIdle, true);
            json.Append(",\"pendingWork\":{");
            WriteInt(json, "blueprints", blueprints, false);
            WriteInt(json, "frames", frames, true);
            WriteInt(json, "haulables", haulables, true);
            WriteInt(json, "activeBills", bills, true);
            WriteInt(json, "designations", Total(designations), true);
            WriteDesignationTypes(json, designations, true);
            json.Append("}");
            WriteObviousBlockers(json, map, enabledWorkers, blueprints + frames, haulables, true);
            json.Append("}");
            return json.ToString();
        }

        private static bool IndexEnabledWork(Pawn pawn, Dictionary<string, int> enabledWorkers)
        {
            if (pawn.workSettings == null)
            {
                return false;
            }
            pawn.workSettings.EnableAndInitializeIfNotAlreadyInitialized();
            bool any = false;
            List<WorkTypeDef> workTypes = DefDatabase<WorkTypeDef>.AllDefsListForReading;
            for (int i = 0; i < workTypes.Count; i++)
            {
                WorkTypeDef work = workTypes[i];
                if (work == null || !work.visible || pawn.WorkTagIsDisabled(work.workTags) || pawn.workSettings.GetPriority(work) <= 0)
                {
                    continue;
                }
                any = true;
                int count;
                enabledWorkers.TryGetValue(work.defName, out count);
                enabledWorkers[work.defName] = count + 1;
            }
            return any;
        }

        private static Dictionary<string, int> CountDesignations(Map map)
        {
            Dictionary<string, int> result = new Dictionary<string, int>();
            List<Designation> all = map.designationManager != null ? map.designationManager.AllDesignations : null;
            if (all == null)
            {
                return result;
            }
            for (int i = 0; i < all.Count; i++)
            {
                Designation designation = all[i];
                if (designation == null || designation.def == null)
                {
                    continue;
                }
                int count;
                result.TryGetValue(designation.def.defName, out count);
                result[designation.def.defName] = count + 1;
            }
            return result;
        }

        private static void WritePawns(StringBuilder json, string name, List<Pawn> pawns, bool comma)
        {
            WriteName(json, name, comma);
            json.Append("[");
            for (int i = 0; i < pawns.Count && i < MaxIdleColonists; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }
                Pawn pawn = pawns[i];
                json.Append("{");
                WriteString(json, "id", pawn.ThingID, false);
                WriteString(json, "name", SafeLabel(pawn), true);
                WriteString(json, "currentJob", pawn.CurJobDef != null ? pawn.CurJobDef.defName : null, true);
                WriteEnabledWork(json, pawn, true);
                json.Append("}");
            }
            json.Append("]");
        }

        private static void WriteEnabledWork(StringBuilder json, Pawn pawn, bool comma)
        {
            WriteName(json, "enabledWorkTypes", comma);
            json.Append("[");
            int written = 0;
            if (pawn.workSettings != null)
            {
                List<WorkTypeDef> workTypes = DefDatabase<WorkTypeDef>.AllDefsListForReading;
                for (int i = 0; i < workTypes.Count && written < MaxWorkTypesPerPawn; i++)
                {
                    WorkTypeDef work = workTypes[i];
                    if (work == null || !work.visible || pawn.WorkTagIsDisabled(work.workTags) || pawn.workSettings.GetPriority(work) <= 0)
                    {
                        continue;
                    }
                    if (written > 0)
                    {
                        json.Append(",");
                    }
                    WriteStringValue(json, work.defName);
                    written++;
                }
            }
            json.Append("]");
        }

        private static void WriteDesignationTypes(StringBuilder json, Dictionary<string, int> designations, bool comma)
        {
            WriteName(json, "byDesignationType", comma);
            json.Append("[");
            List<string> names = new List<string>(designations.Keys);
            names.Sort(StringComparer.Ordinal);
            for (int i = 0; i < names.Count && i < MaxDesignationTypes; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }
                json.Append("{");
                WriteString(json, "defName", names[i], false);
                WriteInt(json, "count", designations[names[i]], true);
                json.Append("}");
            }
            json.Append("]");
        }

        private static void WriteObviousBlockers(StringBuilder json, Map map, Dictionary<string, int> enabledWorkers, int construction, int haulables, bool comma)
        {
            WriteName(json, "obviousBlockers", comma);
            json.Append("[");
            bool wrote = false;
            if (construction > 0 && WorkerCount(enabledWorkers, WorkTypeDefOf.Construction) == 0)
            {
                WriteBlocker(json, "construction", "pending construction has no capable colonist with Construction enabled", ref wrote);
            }
            if (haulables > 0 && WorkerCount(enabledWorkers, WorkTypeDefOf.Hauling) == 0)
            {
                WriteBlocker(json, "hauling", "pending hauling has no capable colonist with Hauling enabled", ref wrote);
            }
            if (Find.ResearchManager != null && Find.ResearchManager.GetProject() != null)
            {
                if (WorkerCount(enabledWorkers, WorkTypeDefOf.Research) == 0)
                {
                    WriteBlocker(json, "research", "active research has no capable colonist with Research enabled", ref wrote);
                }
                else if (map.listerBuildings != null && !map.listerBuildings.ColonistsHaveResearchBench())
                {
                    WriteBlocker(json, "research", "active research has no usable player research bench", ref wrote);
                }
            }
            json.Append("]");
        }

        private static void WriteBlocker(StringBuilder json, string category, string reason, ref bool wrote)
        {
            if (wrote)
            {
                json.Append(",");
            }
            json.Append("{");
            WriteString(json, "category", category, false);
            WriteString(json, "reason", reason, true);
            json.Append("}");
            wrote = true;
        }

        private static int WorkerCount(Dictionary<string, int> workers, WorkTypeDef work)
        {
            int count;
            return work != null && workers.TryGetValue(work.defName, out count) ? count : 0;
        }

        private static int Total(Dictionary<string, int> values)
        {
            int total = 0;
            foreach (int value in values.Values)
            {
                total += value;
            }
            return total;
        }

        private static int SafeCount(Func<int> getter)
        {
            try { return getter(); } catch { return 0; }
        }

        private static string SafeLabel(Pawn pawn)
        {
            try { return pawn.LabelShortCap; } catch { return pawn.ThingID; }
        }

        private static void WriteString(StringBuilder json, string name, string value, bool comma)
        {
            WriteName(json, name, comma);
            WriteStringValue(json, value);
        }

        private static void WriteStringValue(StringBuilder json, string value)
        {
            if (value == null) json.Append("null");
            else json.Append("\"").Append(RimGPTJson.Escape(value)).Append("\"");
        }

        private static void WriteInt(StringBuilder json, string name, int value, bool comma)
        {
            WriteName(json, name, comma);
            json.Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static void WriteName(StringBuilder json, string name, bool comma)
        {
            if (comma) json.Append(",");
            json.Append("\"").Append(name).Append("\":");
        }
    }
}
