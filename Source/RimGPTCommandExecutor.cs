using System.Collections.Generic;
using RimWorld;
using Verse;
using Verse.AI;

namespace RimGPT
{
    public static class RimGPTCommandExecutor
    {
        public static RimGPTCommandExecutionResult Execute(RimGPTCommand command)
        {
            if (Find.TickManager == null)
            {
                return RimGPTCommandExecutionResult.Failure("No active TickManager");
            }

            switch (command.Type)
            {
                case RimGPTCommandType.Pause:
                    return SetSpeed(0);
                case RimGPTCommandType.Unpause:
                    if (Find.TickManager.CurTimeSpeed == TimeSpeed.Paused)
                    {
                        Find.TickManager.CurTimeSpeed = TimeSpeed.Normal;
                    }
                    return RimGPTCommandExecutionResult.Succeeded("Game unpaused");
                case RimGPTCommandType.SetSpeed:
                    return SetSpeed(command.Speed);
                case RimGPTCommandType.Draft:
                    return SetDrafted(command.PawnId, true);
                case RimGPTCommandType.Undraft:
                    return SetDrafted(command.PawnId, false);
                case RimGPTCommandType.Move:
                    return Move(command.PawnId, command.X, command.Z);
                case RimGPTCommandType.SetWorkPriority:
                    return SetWorkPriority(command.PawnId, command.WorkType, command.Priority);
                case RimGPTCommandType.Allow:
                    return SetForbidden(command.ThingId, false);
                case RimGPTCommandType.Forbid:
                    return SetForbidden(command.ThingId, true);
                case RimGPTCommandType.AllowAll:
                    return AllowAll();
                case RimGPTCommandType.SetResearch:
                    return SetResearch(command.ResearchDef);
                case RimGPTCommandType.PrioritizeJob:
                    return PrioritizeJob(command.PawnId, command.TargetId);
                case RimGPTCommandType.DesignateMine:
                    return DesignateMine(command.X, command.Z);
                case RimGPTCommandType.DesignateCut:
                    return DesignatePlant(command.X, command.Z, PlantDesignation.Cut);
                case RimGPTCommandType.DesignateHarvest:
                    return DesignatePlant(command.X, command.Z, PlantDesignation.Harvest);
                case RimGPTCommandType.DesignateHunt:
                    return DesignateHunt(command.ThingId);
                default:
                    return RimGPTCommandExecutionResult.Failure("Unsupported command");
            }
        }

        private static RimGPTCommandExecutionResult SetSpeed(int speed)
        {
            if (speed < 0 || speed > 3)
            {
                return RimGPTCommandExecutionResult.Failure("Speed must be 0, 1, 2, or 3");
            }

            switch (speed)
            {
                case 0:
                    Find.TickManager.CurTimeSpeed = TimeSpeed.Paused;
                    return RimGPTCommandExecutionResult.Succeeded("Game paused");
                case 1:
                    Find.TickManager.CurTimeSpeed = TimeSpeed.Normal;
                    return RimGPTCommandExecutionResult.Succeeded("Speed set to normal");
                case 2:
                    Find.TickManager.CurTimeSpeed = TimeSpeed.Fast;
                    return RimGPTCommandExecutionResult.Succeeded("Speed set to fast");
                case 3:
                    Find.TickManager.CurTimeSpeed = TimeSpeed.Superfast;
                    return RimGPTCommandExecutionResult.Succeeded("Speed set to superfast");
                default:
                    return RimGPTCommandExecutionResult.Failure("Unsupported speed");
            }
        }

        private static RimGPTCommandExecutionResult SetDrafted(string pawnId, bool drafted)
        {
            Pawn pawn;
            RimGPTCommandExecutionResult lookup = TryGetPlayerColonist(pawnId, out pawn);
            if (!lookup.Success)
            {
                return lookup;
            }

            if (pawn.drafter == null || !pawn.drafter.ShowDraftGizmo)
            {
                return RimGPTCommandExecutionResult.Failure("Pawn cannot be drafted");
            }

            if (pawn.drafter.Drafted == drafted)
            {
                return RimGPTCommandExecutionResult.Succeeded(drafted ? "Pawn already drafted" : "Pawn already undrafted");
            }

            pawn.drafter.Drafted = drafted;
            return RimGPTCommandExecutionResult.Succeeded(drafted ? "Pawn drafted" : "Pawn undrafted");
        }

        private static RimGPTCommandExecutionResult Move(string pawnId, int x, int z)
        {
            Pawn pawn;
            RimGPTCommandExecutionResult lookup = TryGetPlayerColonist(pawnId, out pawn);
            if (!lookup.Success)
            {
                return lookup;
            }

            if (pawn.drafter == null || !pawn.drafter.Drafted)
            {
                return RimGPTCommandExecutionResult.Failure("Pawn must be drafted to move");
            }

            Map map = Find.CurrentMap;
            IntVec3 destination = new IntVec3(x, 0, z);

            if (!destination.InBounds(map))
            {
                return RimGPTCommandExecutionResult.Failure("Destination is outside the current map");
            }

            if (destination.Fogged(map))
            {
                return RimGPTCommandExecutionResult.Failure("Destination is not currently available");
            }

            if (!destination.Walkable(map))
            {
                return RimGPTCommandExecutionResult.Failure("Destination is not walkable");
            }

            if (!pawn.CanReach(destination, PathEndMode.OnCell, Danger.Deadly))
            {
                return RimGPTCommandExecutionResult.Failure("Destination cannot be reached");
            }

            Job job = JobMaker.MakeJob(JobDefOf.Goto, destination);
            job.playerForced = true;
            job.locomotionUrgency = LocomotionUrgency.Jog;
            job.expiryInterval = 5000;
            job.checkOverrideOnExpire = true;

            bool accepted = pawn.jobs.TryTakeOrderedJob(job, JobTag.DraftedOrder);
            if (!accepted)
            {
                return RimGPTCommandExecutionResult.Failure("Move order was rejected");
            }

            return RimGPTCommandExecutionResult.Succeeded("Move order issued");
        }

        private static RimGPTCommandExecutionResult SetWorkPriority(string pawnId, string workTypeName, int priority)
        {
            if (priority < 0 || priority > 4)
            {
                return RimGPTCommandExecutionResult.Failure("Priority must be between 0 and 4");
            }

            if (string.IsNullOrEmpty(workTypeName))
            {
                return RimGPTCommandExecutionResult.Failure("Missing workType");
            }

            Pawn pawn;
            RimGPTCommandExecutionResult lookup = TryGetPlayerColonist(pawnId, out pawn);
            if (!lookup.Success)
            {
                return lookup;
            }

            if (pawn.workSettings == null)
            {
                return RimGPTCommandExecutionResult.Failure("Pawn has no work settings");
            }

            WorkTypeDef workType = DefDatabase<WorkTypeDef>.GetNamedSilentFail(workTypeName);
            if (workType == null)
            {
                return RimGPTCommandExecutionResult.Failure("Unknown workType");
            }

            if (pawn.WorkTagIsDisabled(workType.workTags))
            {
                return RimGPTCommandExecutionResult.Failure("Pawn is incapable of this work type");
            }

            pawn.workSettings.EnableAndInitializeIfNotAlreadyInitialized();
            pawn.workSettings.SetPriority(workType, priority);
            return RimGPTCommandExecutionResult.Succeeded("Work priority set");
        }

        private static RimGPTCommandExecutionResult SetForbidden(string thingId, bool forbidden)
        {
            Thing thing;
            RimGPTCommandExecutionResult lookup = TryGetVisibleThing(thingId, out thing);
            if (!lookup.Success)
            {
                return lookup;
            }

            if (!CanToggleForbidden(thing))
            {
                return RimGPTCommandExecutionResult.Failure("Thing cannot normally be allowed or forbidden");
            }

            if (thing.IsForbidden(Faction.OfPlayer) == forbidden)
            {
                return RimGPTCommandExecutionResult.Succeeded(forbidden ? "Thing already forbidden" : "Thing already allowed");
            }

            ForbidUtility.SetForbidden(thing, forbidden, false);
            return RimGPTCommandExecutionResult.Succeeded(forbidden ? "Thing forbidden" : "Thing allowed");
        }

        private static RimGPTCommandExecutionResult AllowAll()
        {
            Map map;
            RimGPTCommandExecutionResult mapResult = TryGetCurrentMap(out map);
            if (!mapResult.Success)
            {
                return mapResult;
            }

            int affected = 0;
            List<Thing> haulables = map.listerThings.ThingsInGroup(ThingRequestGroup.HaulableEver);
            for (int i = 0; i < haulables.Count; i++)
            {
                Thing thing = haulables[i];
                if (!IsVisibleOnCurrentMap(thing, map) || !CanToggleForbidden(thing) || !thing.IsForbidden(Faction.OfPlayer))
                {
                    continue;
                }

                ForbidUtility.SetForbidden(thing, false, false);
                affected++;
            }

            return RimGPTCommandExecutionResult.Succeeded("Allowed " + affected + " visible forbidden thing(s)");
        }

        private static RimGPTCommandExecutionResult SetResearch(string researchDefName)
        {
            if (string.IsNullOrEmpty(researchDefName))
            {
                return RimGPTCommandExecutionResult.Failure("Missing researchDef");
            }

            if (Current.Game == null || Find.ResearchManager == null)
            {
                return RimGPTCommandExecutionResult.Failure("No active research manager");
            }

            ResearchProjectDef project = DefDatabase<ResearchProjectDef>.GetNamedSilentFail(researchDefName);
            if (project == null)
            {
                return RimGPTCommandExecutionResult.Failure("Unknown research project");
            }

            ResearchProjectDef current = Find.ResearchManager.GetProject();
            if (current == project)
            {
                return RimGPTCommandExecutionResult.Succeeded("Research project already selected");
            }

            if (project.IsFinished)
            {
                return RimGPTCommandExecutionResult.Failure("Research project is already completed");
            }

            if (!project.PrerequisitesCompleted)
            {
                return RimGPTCommandExecutionResult.Failure("Research prerequisites are not completed");
            }

            if (!project.CanStartNow)
            {
                return RimGPTCommandExecutionResult.Failure("Research project cannot be started now");
            }

            Find.ResearchManager.SetCurrentProject(project);
            return RimGPTCommandExecutionResult.Succeeded("Research project selected");
        }

        private static RimGPTCommandExecutionResult PrioritizeJob(string pawnId, string targetId)
        {
            Pawn pawn;
            RimGPTCommandExecutionResult pawnLookup = TryGetPlayerColonist(pawnId, out pawn);
            if (!pawnLookup.Success)
            {
                return pawnLookup;
            }

            Thing target;
            RimGPTCommandExecutionResult targetLookup = TryGetVisibleThing(targetId, out target);
            if (!targetLookup.Success)
            {
                return targetLookup;
            }

            if (target.IsForbidden(Faction.OfPlayer))
            {
                return RimGPTCommandExecutionResult.Failure("Target is forbidden; allow it before prioritizing haul");
            }

            if (target.def == null || !target.def.EverHaulable)
            {
                return RimGPTCommandExecutionResult.Failure("prioritizeJob is currently only implemented for unambiguous hauling targets");
            }

            if (!HaulAIUtility.PawnCanAutomaticallyHaul(pawn, target, true))
            {
                return RimGPTCommandExecutionResult.Failure("Pawn cannot haul this target now");
            }

            Job job = HaulAIUtility.HaulToStorageJob(pawn, target, true);
            if (job == null)
            {
                return RimGPTCommandExecutionResult.Failure("No valid storage destination is available for this target");
            }

            job.playerForced = true;
            bool accepted = pawn.jobs.TryTakeOrderedJob(job, null);
            if (!accepted)
            {
                return RimGPTCommandExecutionResult.Failure("Prioritized haul order was rejected");
            }

            return RimGPTCommandExecutionResult.Succeeded("Prioritized haul order issued");
        }

        private static RimGPTCommandExecutionResult DesignateMine(int x, int z)
        {
            Map map;
            IntVec3 cell;
            RimGPTCommandExecutionResult cellResult = TryGetVisibleCell(x, z, out map, out cell);
            if (!cellResult.Success)
            {
                return cellResult;
            }

            Mineable mineable = cell.GetFirstMineable(map);
            if (mineable == null)
            {
                return RimGPTCommandExecutionResult.Failure("Cell does not contain a mineable thing");
            }

            if (map.designationManager.DesignationOn(mineable, DesignationDefOf.Mine) != null)
            {
                return RimGPTCommandExecutionResult.Succeeded("Mine designation already exists");
            }

            Designator_Mine designator = new Designator_Mine();
            AcceptanceReport report = designator.CanDesignateThing(mineable);
            if (!report)
            {
                return RimGPTCommandExecutionResult.Failure(SafeReportReason(report, "Mine designation is not allowed for this target"));
            }

            designator.DesignateThing(mineable);
            return RimGPTCommandExecutionResult.Succeeded("Mine designation added");
        }

        private static RimGPTCommandExecutionResult DesignatePlant(int x, int z, PlantDesignation designation)
        {
            Map map;
            IntVec3 cell;
            RimGPTCommandExecutionResult cellResult = TryGetVisibleCell(x, z, out map, out cell);
            if (!cellResult.Success)
            {
                return cellResult;
            }

            Plant plant = cell.GetPlant(map);
            if (plant == null)
            {
                return RimGPTCommandExecutionResult.Failure("Cell does not contain a plant");
            }

            DesignationDef existingDef = designation == PlantDesignation.Cut ? DesignationDefOf.CutPlant : DesignationDefOf.HarvestPlant;
            if (map.designationManager.DesignationOn(plant, existingDef) != null)
            {
                return RimGPTCommandExecutionResult.Succeeded(designation == PlantDesignation.Cut ? "Cut designation already exists" : "Harvest designation already exists");
            }

            if (designation == PlantDesignation.Cut)
            {
                Designator_PlantsCut designator = new Designator_PlantsCut();
                AcceptanceReport report = designator.CanDesignateThing(plant);
                if (!report)
                {
                    return RimGPTCommandExecutionResult.Failure(SafeReportReason(report, "Cut designation is not allowed for this plant"));
                }

                designator.DesignateThing(plant);
                return RimGPTCommandExecutionResult.Succeeded("Cut designation added");
            }
            else
            {
                if (!plant.HarvestableNow)
                {
                    return RimGPTCommandExecutionResult.Failure("Plant is not harvestable now");
                }

                Designator_PlantsHarvest designator = new Designator_PlantsHarvest();
                AcceptanceReport report = designator.CanDesignateThing(plant);
                if (!report)
                {
                    return RimGPTCommandExecutionResult.Failure(SafeReportReason(report, "Harvest designation is not allowed for this plant"));
                }

                designator.DesignateThing(plant);
                return RimGPTCommandExecutionResult.Succeeded("Harvest designation added");
            }
        }

        private static RimGPTCommandExecutionResult DesignateHunt(string thingId)
        {
            Thing thing;
            RimGPTCommandExecutionResult lookup = TryGetVisibleThing(thingId, out thing);
            if (!lookup.Success)
            {
                return lookup;
            }

            Pawn animal = thing as Pawn;
            if (animal == null || animal.RaceProps == null || !animal.RaceProps.Animal)
            {
                return RimGPTCommandExecutionResult.Failure("Target is not an animal");
            }

            if (animal.Faction == Faction.OfPlayer)
            {
                return RimGPTCommandExecutionResult.Failure("Cannot designate player animals for hunting");
            }

            Map map = Find.CurrentMap;
            if (map.designationManager.DesignationOn(animal, DesignationDefOf.Hunt) != null)
            {
                return RimGPTCommandExecutionResult.Succeeded("Hunt designation already exists");
            }

            Designator_Hunt designator = new Designator_Hunt();
            AcceptanceReport report = designator.CanDesignateThing(animal);
            if (!report)
            {
                return RimGPTCommandExecutionResult.Failure(SafeReportReason(report, "Hunt designation is not allowed for this target"));
            }

            designator.DesignateThing(animal);
            return RimGPTCommandExecutionResult.Succeeded("Hunt designation added");
        }

        private static RimGPTCommandExecutionResult TryGetPlayerColonist(string pawnId, out Pawn pawn)
        {
            pawn = null;

            if (string.IsNullOrEmpty(pawnId))
            {
                return RimGPTCommandExecutionResult.Failure("Missing pawnId");
            }

            if (Current.Game == null || Find.CurrentMap == null)
            {
                return RimGPTCommandExecutionResult.Failure("No current player map is loaded");
            }

            Map map = Find.CurrentMap;
            List<Pawn> colonists = map.mapPawns.FreeColonistsSpawned;
            for (int i = 0; i < colonists.Count; i++)
            {
                Pawn candidate = colonists[i];
                if (candidate != null && candidate.ThingID == pawnId)
                {
                    pawn = candidate;
                    break;
                }
            }

            if (pawn == null)
            {
                return RimGPTCommandExecutionResult.Failure("Pawn was not found on the current player map");
            }

            if (!pawn.Spawned || pawn.Map != map)
            {
                return RimGPTCommandExecutionResult.Failure("Pawn is not spawned on the current player map");
            }

            if (pawn.Dead)
            {
                return RimGPTCommandExecutionResult.Failure("Pawn is dead");
            }

            if (pawn.Faction != Faction.OfPlayer || !pawn.IsColonistPlayerControlled)
            {
                return RimGPTCommandExecutionResult.Failure("Pawn is not a player-controlled colonist");
            }

            return RimGPTCommandExecutionResult.Succeeded("Pawn found");
        }

        private static RimGPTCommandExecutionResult TryGetCurrentMap(out Map map)
        {
            map = null;
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return RimGPTCommandExecutionResult.Failure("No current player map is loaded");
            }

            map = Find.CurrentMap;
            return RimGPTCommandExecutionResult.Succeeded("Map found");
        }

        private static RimGPTCommandExecutionResult TryGetVisibleThing(string thingId, out Thing thing)
        {
            thing = null;
            if (string.IsNullOrEmpty(thingId))
            {
                return RimGPTCommandExecutionResult.Failure("Missing thingId");
            }

            Map map;
            RimGPTCommandExecutionResult mapResult = TryGetCurrentMap(out map);
            if (!mapResult.Success)
            {
                return mapResult;
            }

            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count; i++)
            {
                Thing candidate = things[i];
                if (candidate != null && candidate.ThingID == thingId)
                {
                    thing = candidate;
                    break;
                }
            }

            if (thing == null)
            {
                return RimGPTCommandExecutionResult.Failure("Thing was not found on the current player map");
            }

            if (!IsVisibleOnCurrentMap(thing, map))
            {
                return RimGPTCommandExecutionResult.Failure("Thing is not currently visible on the player map");
            }

            return RimGPTCommandExecutionResult.Succeeded("Thing found");
        }

        private static RimGPTCommandExecutionResult TryGetVisibleCell(int x, int z, out Map map, out IntVec3 cell)
        {
            cell = new IntVec3(x, 0, z);
            RimGPTCommandExecutionResult mapResult = TryGetCurrentMap(out map);
            if (!mapResult.Success)
            {
                return mapResult;
            }

            if (!cell.InBounds(map))
            {
                return RimGPTCommandExecutionResult.Failure("Cell is outside the current map");
            }

            if (cell.Fogged(map))
            {
                return RimGPTCommandExecutionResult.Failure("Cell is not currently visible");
            }

            return RimGPTCommandExecutionResult.Succeeded("Cell found");
        }

        private static bool IsVisibleOnCurrentMap(Thing thing, Map map)
        {
            return thing != null && thing.Spawned && thing.Map == map && !thing.Position.Fogged(map);
        }

        private static bool CanToggleForbidden(Thing thing)
        {
            return thing != null && thing.def != null && thing.def.EverHaulable && thing.TryGetComp<CompForbiddable>() != null;
        }

        private static string SafeReportReason(AcceptanceReport report, string fallback)
        {
            string reason = report.Reason;
            return string.IsNullOrEmpty(reason) ? fallback : reason;
        }

        private enum PlantDesignation
        {
            Cut,
            Harvest
        }
    }

    public sealed class RimGPTCommandExecutionResult
    {
        public bool Success;
        public string Message;

        public static RimGPTCommandExecutionResult Succeeded(string message)
        {
            return new RimGPTCommandExecutionResult
            {
                Success = true,
                Message = message
            };
        }

        public static RimGPTCommandExecutionResult Failure(string error)
        {
            return new RimGPTCommandExecutionResult
            {
                Success = false,
                Message = error
            };
        }
    }
}
