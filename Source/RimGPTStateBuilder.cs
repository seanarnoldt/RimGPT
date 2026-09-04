using System;
using System.Collections.Generic;
using RimWorld;
using UnityEngine;
using Verse;
using Verse.AI;

namespace RimGPT
{
    public static class RimGPTStateBuilder
    {
        public static string BuildJson()
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return "{\"schemaVersion\":1,\"game\":{\"loaded\":false}}";
            }

            Map map = Find.CurrentMap;
            RimGPTStateModel state = new RimGPTStateModel();
            state.Game = BuildGameState(map);
            state.Colony = BuildColonyState(map);
            state.Colonists = BuildColonists(map);
            state.Resources = BuildResources(map);
            state.Research = BuildResearch();
            state.Threats = BuildThreats(map);

            return RimGPTStateJsonWriter.Write(state);
        }

        private static RimGPTGameState BuildGameState(Map map)
        {
            int ticksAbs = Find.TickManager.TicksAbs;
            Vector2 longLat = Vector2.zero;

            try
            {
                if (Find.WorldGrid != null)
                {
                    longLat = Find.WorldGrid.LongLatOf(map.Tile);
                }
            }
            catch
            {
                longLat = Vector2.zero;
            }

            RimGPTGameState game = new RimGPTGameState();
            game.Loaded = true;
            game.Paused = Find.TickManager.Paused;
            game.Speed = (int)Find.TickManager.CurTimeSpeed;
            game.TicksGame = Find.TickManager.TicksGame;
            game.Date = SafeString(delegate { return GenDate.DateFullStringAt(ticksAbs, longLat); });
            game.TimeOfDay = SafeString(delegate { return GenDate.HourOfDay(ticksAbs, longLat.x).ToString("00") + ":00"; });
            game.CurrentMapId = "map-" + map.uniqueID;
            return game;
        }

        private static RimGPTColonyState BuildColonyState(Map map)
        {
            RimGPTColonyState colony = new RimGPTColonyState();
            colony.ColonistCount = SafeCount(delegate { return map.mapPawns.FreeColonistsSpawned.Count; });
            colony.PrisonerCount = SafeCount(delegate { return map.mapPawns.PrisonersOfColonySpawned.Count; });
            colony.AnimalCount = SafeCount(delegate { return map.mapPawns.SpawnedColonyAnimals.Count; });
            return colony;
        }

        private static List<RimGPTColonistState> BuildColonists(Map map)
        {
            List<RimGPTColonistState> result = new List<RimGPTColonistState>();
            List<Pawn> colonists = map.mapPawns.FreeColonistsSpawned;

            for (int i = 0; i < colonists.Count; i++)
            {
                Pawn pawn = colonists[i];
                try
                {
                    result.Add(BuildColonist(pawn));
                }
                catch (Exception ex)
                {
                    Log.Error("[RimGPT] Exception serializing colonist '" + SafePawnId(pawn) + "': " + ex);
                }
            }

            return result;
        }

        private static RimGPTColonistState BuildColonist(Pawn pawn)
        {
            RimGPTColonistState colonist = new RimGPTColonistState();
            colonist.Id = SafePawnId(pawn);
            colonist.Name = SafeLabel(pawn);
            colonist.KindDef = pawn.kindDef != null ? pawn.kindDef.defName : null;
            colonist.Gender = pawn.gender.ToString();
            colonist.Age = SafeInt(delegate { return pawn.ageTracker != null ? pawn.ageTracker.AgeBiologicalYears : 0; });
            colonist.Position = BuildPosition(pawn.Position);
            colonist.Drafted = pawn.Drafted;
            colonist.CurrentJob = BuildCurrentJob(pawn);
            colonist.Health = BuildHealth(pawn);
            colonist.Needs = BuildNeeds(pawn);
            colonist.Skills = BuildSkills(pawn);
            colonist.WorkPriorities = BuildWorkPriorities(pawn);
            colonist.Equipment = BuildEquipment(pawn);
            return colonist;
        }

        private static RimGPTJobState BuildCurrentJob(Pawn pawn)
        {
            Job job = pawn.CurJob;
            if (job == null || job.def == null)
            {
                return null;
            }

            RimGPTJobState currentJob = new RimGPTJobState();
            currentJob.DefName = job.def.defName;
            currentJob.Label = job.def.label;
            return currentJob;
        }

        private static RimGPTHealthState BuildHealth(Pawn pawn)
        {
            RimGPTHealthState health = new RimGPTHealthState();
            health.Downed = pawn.Downed;
            health.Dead = pawn.Dead;
            health.BleedingRate = SafeFloat(delegate { return pawn.health != null ? pawn.health.hediffSet.BleedRateTotal : 0f; });
            health.Pain = SafeFloat(delegate { return pawn.health != null ? pawn.health.hediffSet.PainTotal : 0f; });
            health.Hediffs = new List<RimGPTHediffState>();

            if (pawn.Dead)
            {
                health.Summary = "dead";
            }
            else if (pawn.Downed)
            {
                health.Summary = "downed";
            }
            else if (health.BleedingRate > 0f || health.Pain > 0.15f)
            {
                health.Summary = "injured";
            }
            else
            {
                health.Summary = "healthy";
            }

            if (pawn.health != null && pawn.health.hediffSet != null && pawn.health.hediffSet.hediffs != null)
            {
                List<Hediff> hediffs = pawn.health.hediffSet.hediffs;
                for (int i = 0; i < hediffs.Count; i++)
                {
                    Hediff hediff = hediffs[i];
                    if (hediff == null || hediff.def == null)
                    {
                        continue;
                    }

                    RimGPTHediffState hediffState = new RimGPTHediffState();
                    hediffState.DefName = hediff.def.defName;
                    hediffState.Label = hediff.LabelBase;
                    hediffState.Severity = hediff.Severity;
                    health.Hediffs.Add(hediffState);
                }
            }

            return health;
        }

        private static RimGPTNeedsState BuildNeeds(Pawn pawn)
        {
            RimGPTNeedsState needs = new RimGPTNeedsState();
            if (pawn.needs == null)
            {
                return needs;
            }

            needs.Mood = NeedLevel(pawn.needs.mood);
            needs.Food = NeedLevel(pawn.needs.food);
            needs.Rest = NeedLevel(pawn.needs.rest);
            needs.Recreation = NeedLevel(pawn.needs.joy);
            return needs;
        }

        private static List<RimGPTSkillState> BuildSkills(Pawn pawn)
        {
            List<RimGPTSkillState> result = new List<RimGPTSkillState>();
            if (pawn.skills == null || pawn.skills.skills == null)
            {
                return result;
            }

            List<SkillRecord> skills = pawn.skills.skills;
            for (int i = 0; i < skills.Count; i++)
            {
                SkillRecord skill = skills[i];
                if (skill == null || skill.def == null)
                {
                    continue;
                }

                RimGPTSkillState skillState = new RimGPTSkillState();
                skillState.DefName = skill.def.defName;
                skillState.Level = skill.Level;
                skillState.Passion = skill.passion.ToString();
                result.Add(skillState);
            }

            return result;
        }

        private static List<RimGPTWorkPriorityState> BuildWorkPriorities(Pawn pawn)
        {
            List<RimGPTWorkPriorityState> result = new List<RimGPTWorkPriorityState>();
            if (pawn.workSettings == null)
            {
                return result;
            }

            List<WorkTypeDef> workTypes = DefDatabase<WorkTypeDef>.AllDefsListForReading;
            for (int i = 0; i < workTypes.Count; i++)
            {
                WorkTypeDef workType = workTypes[i];
                if (workType == null)
                {
                    continue;
                }

                RimGPTWorkPriorityState priority = new RimGPTWorkPriorityState();
                priority.DefName = workType.defName;
                priority.Priority = SafeInt(delegate { return pawn.workSettings.GetPriority(workType); });
                result.Add(priority);
            }

            return result;
        }

        private static List<RimGPTEquipmentState> BuildEquipment(Pawn pawn)
        {
            List<RimGPTEquipmentState> result = new List<RimGPTEquipmentState>();
            if (pawn.equipment == null || pawn.equipment.AllEquipmentListForReading == null)
            {
                return result;
            }

            List<ThingWithComps> equipment = pawn.equipment.AllEquipmentListForReading;
            for (int i = 0; i < equipment.Count; i++)
            {
                ThingWithComps thing = equipment[i];
                if (thing == null || thing.def == null)
                {
                    continue;
                }

                RimGPTEquipmentState equipmentState = new RimGPTEquipmentState();
                equipmentState.Id = SafeThingId(thing);
                equipmentState.DefName = thing.def.defName;
                equipmentState.Label = thing.LabelCap;
                result.Add(equipmentState);
            }

            return result;
        }

        private static RimGPTResourcesState BuildResources(Map map)
        {
            RimGPTResourcesState resources = new RimGPTResourcesState();
            resources.Silver = CountResource(map, "Silver");
            resources.Wood = CountResource(map, "WoodLog");
            resources.Steel = CountResource(map, "Steel");
            resources.Plasteel = CountResource(map, "Plasteel");
            resources.Components = CountResource(map, "ComponentIndustrial");
            resources.AdvancedComponents = CountResource(map, "ComponentSpacer");
            resources.Medicine = CountResource(map, "MedicineHerbal") + CountResource(map, "MedicineIndustrial") + CountResource(map, "MedicineUltratech");
            resources.IndustrialMedicine = CountResource(map, "MedicineIndustrial");
            resources.GlitterworldMedicine = CountResource(map, "MedicineUltratech");
            resources.Food = BuildFoodState(map);
            return resources;
        }

        private static int CountResource(Map map, string defName)
        {
            ThingDef def = DefDatabase<ThingDef>.GetNamedSilentFail(defName);
            if (def == null || map.resourceCounter == null)
            {
                return 0;
            }

            return SafeInt(delegate { return map.resourceCounter.GetCount(def); });
        }

        private static RimGPTFoodState BuildFoodState(Map map)
        {
            RimGPTFoodState food = new RimGPTFoodState();
            if (map.listerThings == null)
            {
                return food;
            }

            List<Thing> foodThings = map.listerThings.ThingsInGroup(ThingRequestGroup.FoodSourceNotPlantOrTree);
            for (int i = 0; i < foodThings.Count; i++)
            {
                Thing thing = foodThings[i];
                if (thing == null || thing.def == null || thing.Position.Fogged(map) || thing.IsForbidden(Faction.OfPlayer))
                {
                    continue;
                }

                if (thing.def.IsNutritionGivingIngestible)
                {
                    food.TotalNutrition += thing.GetStatValue(StatDefOf.Nutrition) * thing.stackCount;
                }

                if (thing.def.ingestible != null && thing.def.ingestible.IsMeal)
                {
                    food.Meals += thing.stackCount;
                }
            }

            return food;
        }

        private static RimGPTResearchState BuildResearch()
        {
            RimGPTResearchState research = new RimGPTResearchState();
            if (Find.ResearchManager == null || Find.ResearchManager.GetProject() == null)
            {
                return research;
            }

            ResearchProjectDef project = Find.ResearchManager.GetProject();
            research.Current = new RimGPTResearchProjectState();
            research.Current.DefName = project.defName;
            research.Current.Label = project.label;
            research.Current.Progress = SafeFloat(delegate { return Find.ResearchManager.GetProgress(project); });
            research.Current.Cost = project.baseCost;
            return research;
        }

        private static List<RimGPTThreatState> BuildThreats(Map map)
        {
            List<RimGPTThreatState> result = new List<RimGPTThreatState>();
            IReadOnlyList<Pawn> pawns = map.mapPawns.AllPawnsSpawned;

            for (int i = 0; i < pawns.Count; i++)
            {
                Pawn pawn = pawns[i];
                try
                {
                    if (pawn == null || pawn.Dead || pawn.Faction == null || !pawn.HostileTo(Faction.OfPlayer) || pawn.Position.Fogged(map))
                    {
                        continue;
                    }

                    RimGPTThreatState threat = new RimGPTThreatState();
                    threat.Id = SafePawnId(pawn);
                    threat.Type = "pawn";
                    threat.DefName = pawn.def != null ? pawn.def.defName : null;
                    threat.Label = SafeLabel(pawn);
                    threat.Faction = pawn.Faction != null ? pawn.Faction.Name : null;
                    threat.Position = BuildPosition(pawn.Position);
                    threat.Downed = pawn.Downed;
                    threat.Weapon = BuildPrimaryWeapon(pawn);
                    result.Add(threat);
                }
                catch (Exception ex)
                {
                    Log.Error("[RimGPT] Exception serializing threat '" + SafePawnId(pawn) + "': " + ex);
                }
            }

            return result;
        }

        private static RimGPTEquipmentState BuildPrimaryWeapon(Pawn pawn)
        {
            if (pawn.equipment == null || pawn.equipment.Primary == null || pawn.equipment.Primary.def == null)
            {
                return null;
            }

            ThingWithComps weapon = pawn.equipment.Primary;
            RimGPTEquipmentState result = new RimGPTEquipmentState();
            result.Id = SafeThingId(weapon);
            result.DefName = weapon.def.defName;
            result.Label = weapon.LabelCap;
            return result;
        }

        private static RimGPTPositionState BuildPosition(IntVec3 position)
        {
            RimGPTPositionState result = new RimGPTPositionState();
            result.X = position.x;
            result.Z = position.z;
            return result;
        }

        private static float? NeedLevel(Need need)
        {
            if (need == null)
            {
                return null;
            }

            return need.CurLevelPercentage;
        }

        private static string SafePawnId(Pawn pawn)
        {
            return pawn != null ? SafeThingId(pawn) : null;
        }

        private static string SafeThingId(Thing thing)
        {
            if (thing == null)
            {
                return null;
            }

            return thing.ThingID;
        }

        private static string SafeLabel(Thing thing)
        {
            return thing != null ? thing.LabelShortCap : null;
        }

        private static int SafeCount(Func<int> getter)
        {
            return SafeInt(getter);
        }

        private static int SafeInt(Func<int> getter)
        {
            try
            {
                return getter();
            }
            catch
            {
                return 0;
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
    }
}
