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
        public static string BuildJson(long snapshotVersion, string capturedAtUtc, int snapshotTicksGame)
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return "{\"schemaVersion\":2,\"snapshot\":{\"version\":" + snapshotVersion + ",\"capturedAtUtc\":\"" + RimGPTJson.Escape(capturedAtUtc) + "\",\"ticksGame\":" + snapshotTicksGame + "},\"game\":{\"loaded\":false}}";
            }

            Map map = Find.CurrentMap;
            RimGPTStateModel state = new RimGPTStateModel();
            state.Snapshot = new RimGPTSnapshotState
            {
                Version = snapshotVersion,
                CapturedAtUtc = capturedAtUtc,
                TicksGame = snapshotTicksGame
            };
            state.Game = BuildGameState(map);
            state.Colony = BuildColonyState(map);
            state.Colonists = BuildColonists(map);
            state.Resources = BuildResources(map);
            state.MapThings = BuildMapThings(map);
            state.Research = BuildResearch();
            state.Threats = BuildThreats(map);
            state.MapJson = RimGPTSpatialJson.BuildMapOverviewJson(map);
            state.BuildingsJson = RimGPTSpatialJson.BuildBuildingsJson(map);
            state.OperationsJson = RimGPTOperationalJson.Build(map);

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
            game.ColonyLineageId = SafeString(delegate
            {
                RimGPTGameComponent component = Current.Game.GetComponent<RimGPTGameComponent>();
                return component != null ? component.ColonyLineageId : null;
            });
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
            colonist.Work = BuildWork(pawn);
            colonist.WorkPriorities = BuildWorkPriorities(pawn);
            colonist.PrimaryEquipment = BuildPrimaryWeapon(pawn);
            colonist.Equipment = BuildEquipment(pawn);
            colonist.Apparel = BuildApparel(pawn);
            colonist.AssignedBed = BuildAssignedBed(pawn);
            colonist.AllowedArea = BuildAllowedArea(pawn);
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

            pawn.workSettings.EnableAndInitializeIfNotAlreadyInitialized();
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

        private static List<RimGPTWorkState> BuildWork(Pawn pawn)
        {
            List<RimGPTWorkState> result = new List<RimGPTWorkState>();
            if (pawn.workSettings == null)
            {
                return result;
            }

            pawn.workSettings.EnableAndInitializeIfNotAlreadyInitialized();
            List<WorkTypeDef> workTypes = DefDatabase<WorkTypeDef>.AllDefsListForReading;
            for (int i = 0; i < workTypes.Count; i++)
            {
                WorkTypeDef workType = workTypes[i];
                if (workType == null || !workType.visible)
                {
                    continue;
                }

                bool disabled = SafeBool(delegate { return pawn.WorkTagIsDisabled(workType.workTags); });
                int priority = SafeInt(delegate { return pawn.workSettings.GetPriority(workType); });
                RimGPTWorkState work = new RimGPTWorkState();
                work.DefName = workType.defName;
                work.Label = !string.IsNullOrEmpty(workType.labelShort) ? workType.labelShort : workType.label;
                work.Disabled = disabled || priority == 0;
                work.Capable = !disabled;
                work.DisabledReason = disabled ? "Pawn is incapable of this work type" : null;
                work.Priority = priority;
                result.Add(work);
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
                equipmentState.Quality = QualityOf(thing);
                equipmentState.HitPoints = thing.HitPoints;
                equipmentState.MaxHitPoints = thing.MaxHitPoints;
                result.Add(equipmentState);
            }

            return result;
        }

        private static List<RimGPTApparelState> BuildApparel(Pawn pawn)
        {
            List<RimGPTApparelState> result = new List<RimGPTApparelState>();
            if (pawn.apparel == null || pawn.apparel.WornApparel == null)
            {
                return result;
            }

            List<Apparel> worn = pawn.apparel.WornApparel;
            for (int i = 0; i < worn.Count; i++)
            {
                Apparel apparel = worn[i];
                if (apparel == null || apparel.def == null)
                {
                    continue;
                }

                result.Add(new RimGPTApparelState
                {
                    Id = SafeThingId(apparel),
                    DefName = apparel.def.defName,
                    Label = apparel.LabelCap,
                    Quality = QualityOf(apparel),
                    HitPoints = apparel.HitPoints,
                    MaxHitPoints = apparel.MaxHitPoints,
                    Tainted = apparel.WornByCorpse
                });
            }

            return result;
        }

        private static RimGPTBedAssignmentState BuildAssignedBed(Pawn pawn)
        {
            Building_Bed bed = pawn.ownership != null ? pawn.ownership.OwnedBed : null;
            if (bed == null || bed.def == null || !bed.Spawned || bed.Map != pawn.Map)
            {
                return null;
            }

            return new RimGPTBedAssignmentState
            {
                Id = SafeThingId(bed),
                DefName = bed.def.defName,
                Position = BuildPosition(bed.Position)
            };
        }

        private static RimGPTAreaAssignmentState BuildAllowedArea(Pawn pawn)
        {
            if (pawn.playerSettings == null || !pawn.playerSettings.SupportsAllowedAreas)
            {
                return null;
            }

            Area area = pawn.playerSettings.AreaRestrictionInPawnCurrentMap;
            if (area == null)
            {
                return null;
            }

            return new RimGPTAreaAssignmentState
            {
                Id = RimGPTOperationalJson.AreaId(area),
                Label = area.Label
            };
        }

        private static RimGPTResourcesState BuildResources(Map map)
        {
            RimGPTResourceVisibility visibility = CountVisibleResources(map);
            RimGPTResourcesState resources = new RimGPTResourcesState();
            resources.Silver = visibility.Available.Silver;
            resources.Wood = visibility.Available.Wood;
            resources.Steel = visibility.Available.Steel;
            resources.Plasteel = visibility.Available.Plasteel;
            resources.Components = visibility.Available.Components;
            resources.AdvancedComponents = visibility.Available.AdvancedComponents;
            resources.Medicine = visibility.Available.Medicine;
            resources.IndustrialMedicine = visibility.Available.IndustrialMedicine;
            resources.GlitterworldMedicine = visibility.Available.GlitterworldMedicine;
            resources.Food = visibility.Available.Food;
            resources.Available = visibility.Available;
            resources.Forbidden = visibility.Forbidden;
            resources.TotalVisible = visibility.TotalVisible;
            return resources;
        }

        private static RimGPTResourceVisibility CountVisibleResources(Map map)
        {
            RimGPTResourceVisibility visibility = new RimGPTResourceVisibility();
            visibility.Available = NewResourceCounts();
            visibility.Forbidden = NewResourceCounts();
            visibility.TotalVisible = NewResourceCounts();

            if (map.listerThings == null)
            {
                return visibility;
            }

            List<Thing> haulables = map.listerThings.ThingsInGroup(ThingRequestGroup.HaulableEver);
            for (int i = 0; i < haulables.Count; i++)
            {
                Thing thing = haulables[i];
                if (!IsVisibleOnMap(thing, map) || thing.def == null)
                {
                    continue;
                }

                bool forbidden = thing.IsForbidden(Faction.OfPlayer);
                AddResourceThing(visibility.TotalVisible, thing);
                if (forbidden)
                {
                    AddResourceThing(visibility.Forbidden, thing);
                }
                else
                {
                    AddResourceThing(visibility.Available, thing);
                }
            }

            return visibility;
        }

        private static RimGPTResourceCountsState NewResourceCounts()
        {
            RimGPTResourceCountsState counts = new RimGPTResourceCountsState();
            counts.Food = new RimGPTFoodState();
            return counts;
        }

        private static void AddResourceThing(RimGPTResourceCountsState counts, Thing thing)
        {
            string defName = thing.def.defName;
            int stackCount = thing.stackCount;

            if (defName == "Silver")
            {
                counts.Silver += stackCount;
            }
            else if (defName == "WoodLog")
            {
                counts.Wood += stackCount;
            }
            else if (defName == "Steel")
            {
                counts.Steel += stackCount;
            }
            else if (defName == "Plasteel")
            {
                counts.Plasteel += stackCount;
            }
            else if (defName == "ComponentIndustrial")
            {
                counts.Components += stackCount;
            }
            else if (defName == "ComponentSpacer")
            {
                counts.AdvancedComponents += stackCount;
            }
            else if (defName == "MedicineHerbal")
            {
                counts.Medicine += stackCount;
            }
            else if (defName == "MedicineIndustrial")
            {
                counts.Medicine += stackCount;
                counts.IndustrialMedicine += stackCount;
            }
            else if (defName == "MedicineUltratech")
            {
                counts.Medicine += stackCount;
                counts.GlitterworldMedicine += stackCount;
            }

            if (thing.def.IsNutritionGivingIngestible)
            {
                counts.Food.TotalNutrition += thing.GetStatValue(StatDefOf.Nutrition) * stackCount;
            }

            if (thing.def.ingestible != null && thing.def.ingestible.IsMeal)
            {
                counts.Food.Meals += stackCount;
            }
        }

        private static RimGPTMapThingsState BuildMapThings(Map map)
        {
            const int MaxThingsPerList = 120;
            RimGPTMapThingsState result = new RimGPTMapThingsState();
            result.Forbidden = new List<RimGPTMapThingState>();
            result.Haulable = new List<RimGPTMapThingState>();

            if (map.listerThings == null)
            {
                return result;
            }

            List<Thing> haulables = map.listerThings.ThingsInGroup(ThingRequestGroup.HaulableEver);
            for (int i = 0; i < haulables.Count; i++)
            {
                Thing thing = haulables[i];
                if (!IsVisibleOnMap(thing, map) || !IsUsefulMapThing(thing))
                {
                    continue;
                }

                bool forbidden = thing.IsForbidden(Faction.OfPlayer);
                if (forbidden && result.Forbidden.Count < MaxThingsPerList)
                {
                    result.Forbidden.Add(BuildMapThing(thing, true));
                }

                if (result.Haulable.Count < MaxThingsPerList)
                {
                    result.Haulable.Add(BuildMapThing(thing, forbidden));
                }

                if (result.Forbidden.Count >= MaxThingsPerList && result.Haulable.Count >= MaxThingsPerList)
                {
                    break;
                }
            }

            return result;
        }

        private static RimGPTMapThingState BuildMapThing(Thing thing, bool forbidden)
        {
            RimGPTMapThingState result = new RimGPTMapThingState();
            result.Id = SafeThingId(thing);
            result.DefName = thing.def != null ? thing.def.defName : null;
            result.Label = SafeLabel(thing);
            result.StackCount = thing.stackCount;
            result.Position = BuildPosition(thing.Position);
            result.Forbidden = forbidden;
            return result;
        }

        private static bool IsUsefulMapThing(Thing thing)
        {
            if (thing == null || thing.def == null || !thing.def.EverHaulable)
            {
                return false;
            }

            if (thing.def.IsNutritionGivingIngestible || thing.def.IsWeapon || thing.def.IsMedicine)
            {
                return true;
            }

            string defName = thing.def.defName;
            return defName == "Silver"
                || defName == "WoodLog"
                || defName == "Steel"
                || defName == "Plasteel"
                || defName == "ComponentIndustrial"
                || defName == "ComponentSpacer"
                || thing.def.category == ThingCategory.Item;
        }

        private static bool IsVisibleOnMap(Thing thing, Map map)
        {
            return thing != null && thing.Spawned && thing.Map == map && !thing.Position.Fogged(map);
        }

        private static RimGPTResearchState BuildResearch()
        {
            RimGPTResearchState research = new RimGPTResearchState();
            research.Available = BuildAvailableResearchProjects();
            if (Find.ResearchManager == null || Find.ResearchManager.GetProject() == null)
            {
                return research;
            }

            ResearchProjectDef project = Find.ResearchManager.GetProject();
            research.Current = BuildResearchProject(project);
            return research;
        }

        private static List<RimGPTResearchProjectState> BuildAvailableResearchProjects()
        {
            const int MaxAvailableResearchProjects = 80;
            List<RimGPTResearchProjectState> result = new List<RimGPTResearchProjectState>();
            if (Find.ResearchManager == null)
            {
                return result;
            }

            List<ResearchProjectDef> projects = DefDatabase<ResearchProjectDef>.AllDefsListForReading;
            for (int i = 0; i < projects.Count; i++)
            {
                ResearchProjectDef project = projects[i];
                if (project == null || project.IsFinished || project.IsHidden || !project.CanStartNow)
                {
                    continue;
                }

                result.Add(BuildResearchProject(project));
                if (result.Count >= MaxAvailableResearchProjects)
                {
                    break;
                }
            }

            return result;
        }

        private static RimGPTResearchProjectState BuildResearchProject(ResearchProjectDef project)
        {
            RimGPTResearchProjectState result = new RimGPTResearchProjectState();
            result.DefName = project.defName;
            result.Label = project.label;
            result.Progress = SafeFloat(delegate { return Find.ResearchManager != null ? Find.ResearchManager.GetProgress(project) : 0f; });
            result.Cost = project.baseCost;
            return result;
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
            result.Quality = QualityOf(weapon);
            result.HitPoints = weapon.HitPoints;
            result.MaxHitPoints = weapon.MaxHitPoints;
            return result;
        }

        private static string QualityOf(Thing thing)
        {
            CompQuality quality = thing != null ? thing.TryGetComp<CompQuality>() : null;
            return quality != null ? quality.Quality.ToString() : null;
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

        private sealed class RimGPTResourceVisibility
        {
            public RimGPTResourceCountsState Available;
            public RimGPTResourceCountsState Forbidden;
            public RimGPTResourceCountsState TotalVisible;
        }
    }
}
