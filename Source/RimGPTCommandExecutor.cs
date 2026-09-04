using System.Collections.Generic;
using System.Globalization;
using System.Text;
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
                    return RimGPTCommandExecutionResult.Succeeded("Game unpaused", SpeedDataJson(1));
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
                case RimGPTCommandType.CreateStockpile:
                    return CreateStockpile(command.MinX, command.MinZ, command.MaxX, command.MaxZ);
                case RimGPTCommandType.SetStockpilePriority:
                    return SetStockpilePriority(command.ZoneId, command.StoragePriority);
                case RimGPTCommandType.SetStockpilePreset:
                    return SetStockpilePreset(command.ZoneId, command.Preset);
                case RimGPTCommandType.CreateGrowingZone:
                    return CreateGrowingZone(command.MinX, command.MinZ, command.MaxX, command.MaxZ);
                case RimGPTCommandType.SetGrowingZonePlant:
                    return SetGrowingZonePlant(command.ZoneId, command.PlantDef);
                case RimGPTCommandType.PlaceBlueprint:
                    return PlaceBlueprint(command.BuildDef, command.X, command.Z, command.Rotation, command.StuffDef);
                case RimGPTCommandType.PlaceBlueprints:
                    return PlaceBlueprints(command.Placements);
                case RimGPTCommandType.CancelAt:
                    return CancelAt(command.X, command.Z);
                case RimGPTCommandType.DesignateDeconstruct:
                    return DesignateDeconstruct(command.ThingId);
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
                    return RimGPTCommandExecutionResult.Succeeded("Game paused", SpeedDataJson(0));
                case 1:
                    Find.TickManager.CurTimeSpeed = TimeSpeed.Normal;
                    return RimGPTCommandExecutionResult.Succeeded("Speed set to normal", SpeedDataJson(1));
                case 2:
                    Find.TickManager.CurTimeSpeed = TimeSpeed.Fast;
                    return RimGPTCommandExecutionResult.Succeeded("Speed set to fast", SpeedDataJson(2));
                case 3:
                    Find.TickManager.CurTimeSpeed = TimeSpeed.Superfast;
                    return RimGPTCommandExecutionResult.Succeeded("Speed set to superfast", SpeedDataJson(3));
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

            return RimGPTCommandExecutionResult.Succeeded(
                "Allowed " + affected + " visible forbidden thing(s)",
                "{\"affected\":" + affected.ToString(CultureInfo.InvariantCulture) + "}"
            );
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

        private static RimGPTCommandExecutionResult CreateStockpile(int minX, int minZ, int maxX, int maxZ)
        {
            Map map;
            RimGPTCommandExecutionResult mapResult = TryGetCurrentMap(out map);
            if (!mapResult.Success)
            {
                return mapResult;
            }

            Zone_Stockpile zone = new Zone_Stockpile(StorageSettingsPreset.DefaultStockpile, map.zoneManager);
            map.zoneManager.RegisterZone(zone);
            int added = AddValidZoneCells(zone, map, minX, minZ, maxX, maxZ, false);
            if (added == 0)
            {
                zone.Delete();
                return RimGPTCommandExecutionResult.Failure("No valid visible stockpile cells were available");
            }

            string data = "{\"zoneId\":\"" + RimGPTJson.Escape(RimGPTSpatialJson.ZoneId(zone)) + "\",\"cellsAdded\":" + added.ToString(CultureInfo.InvariantCulture) + "}";
            return RimGPTCommandExecutionResult.Succeeded("Stockpile created", data);
        }

        private static RimGPTCommandExecutionResult SetStockpilePriority(string zoneId, string priorityName)
        {
            Zone zone;
            RimGPTCommandExecutionResult lookup = TryGetZone(zoneId, out zone);
            if (!lookup.Success)
            {
                return lookup;
            }

            Zone_Stockpile stockpile = zone as Zone_Stockpile;
            if (stockpile == null)
            {
                return RimGPTCommandExecutionResult.Failure("Zone is not a stockpile");
            }

            StoragePriority priority;
            if (!TryParseStoragePriority(priorityName, out priority))
            {
                return RimGPTCommandExecutionResult.Failure("Unknown storage priority");
            }

            stockpile.settings.Priority = priority;
            return RimGPTCommandExecutionResult.Succeeded("Stockpile priority set");
        }

        private static RimGPTCommandExecutionResult SetStockpilePreset(string zoneId, string presetName)
        {
            Zone zone;
            RimGPTCommandExecutionResult lookup = TryGetZone(zoneId, out zone);
            if (!lookup.Success)
            {
                return lookup;
            }

            Zone_Stockpile stockpile = zone as Zone_Stockpile;
            if (stockpile == null)
            {
                return RimGPTCommandExecutionResult.Failure("Zone is not a stockpile");
            }

            string preset = (presetName ?? string.Empty).Trim();
            ThingFilter filter = stockpile.settings.filter;
            if (preset.Equals("all", System.StringComparison.OrdinalIgnoreCase))
            {
                filter.SetAllowAll(null, false);
            }
            else if (preset.Equals("nothing", System.StringComparison.OrdinalIgnoreCase))
            {
                filter.SetDisallowAll(null, null);
            }
            else
            {
                filter.SetDisallowAll(null, null);
                string categoryDefName = StoragePresetCategory(preset);
                ThingCategoryDef category = DefDatabase<ThingCategoryDef>.GetNamedSilentFail(categoryDefName);
                if (category == null)
                {
                    return RimGPTCommandExecutionResult.Failure("Unknown stockpile preset");
                }

                filter.SetAllow(category, true, null, null);
            }

            return RimGPTCommandExecutionResult.Succeeded("Stockpile preset set");
        }

        private static RimGPTCommandExecutionResult CreateGrowingZone(int minX, int minZ, int maxX, int maxZ)
        {
            Map map;
            RimGPTCommandExecutionResult mapResult = TryGetCurrentMap(out map);
            if (!mapResult.Success)
            {
                return mapResult;
            }

            Zone_Growing zone = new Zone_Growing(map.zoneManager);
            map.zoneManager.RegisterZone(zone);
            int added = AddValidZoneCells(zone, map, minX, minZ, maxX, maxZ, true);
            if (added == 0)
            {
                zone.Delete();
                return RimGPTCommandExecutionResult.Failure("No valid visible growing-zone cells were available");
            }

            ThingDef plant = zone.GetPlantDefToGrow();
            string data = "{\"zoneId\":\"" + RimGPTJson.Escape(RimGPTSpatialJson.ZoneId(zone)) + "\",\"cellsAdded\":" + added.ToString(CultureInfo.InvariantCulture) + ",\"plantDef\":";
            data += plant != null ? "\"" + RimGPTJson.Escape(plant.defName) + "\"}" : "null}";
            return RimGPTCommandExecutionResult.Succeeded("Growing zone created", data);
        }

        private static string SpeedDataJson(int requestedSpeed)
        {
            return "{\"requestedSpeed\":" + requestedSpeed.ToString(CultureInfo.InvariantCulture) + "}";
        }

        private static RimGPTCommandExecutionResult SetGrowingZonePlant(string zoneId, string plantDefName)
        {
            Zone zone;
            RimGPTCommandExecutionResult lookup = TryGetZone(zoneId, out zone);
            if (!lookup.Success)
            {
                return lookup;
            }

            Zone_Growing growing = zone as Zone_Growing;
            if (growing == null)
            {
                return RimGPTCommandExecutionResult.Failure("Zone is not a growing zone");
            }

            ThingDef plantDef = DefDatabase<ThingDef>.GetNamedSilentFail(plantDefName);
            if (plantDef == null || plantDef.plant == null || !plantDef.plant.Sowable)
            {
                return RimGPTCommandExecutionResult.Failure("Unknown or unsowable plantDef");
            }

            bool anyValidCell = false;
            foreach (IntVec3 cell in growing.Cells)
            {
                if (PlantUtility.CanNowPlantAt(plantDef, cell, growing.Map, false))
                {
                    anyValidCell = true;
                    break;
                }
            }

            if (!anyValidCell)
            {
                return RimGPTCommandExecutionResult.Failure("Plant cannot currently be sown in this growing zone");
            }

            growing.SetPlantDefToGrow(plantDef);
            return RimGPTCommandExecutionResult.Succeeded("Growing zone plant set");
        }

        private static RimGPTCommandExecutionResult PlaceBlueprint(string buildDefName, int x, int z, string rotationName, string stuffDefName)
        {
            BlueprintPlacementResult result = TryPlaceBlueprint(buildDefName, x, z, rotationName, stuffDefName);
            if (!result.Success)
            {
                return RimGPTCommandExecutionResult.Failure(result.Message);
            }

            return RimGPTCommandExecutionResult.Succeeded(result.Message, result.DataJson);
        }

        private static RimGPTCommandExecutionResult PlaceBlueprints(List<RimGPTBlueprintPlacement> placements)
        {
            if (placements == null || placements.Count == 0)
            {
                return RimGPTCommandExecutionResult.Failure("No placements supplied");
            }

            if (placements.Count > 100)
            {
                return RimGPTCommandExecutionResult.Failure("Too many placements");
            }

            StringBuilder data = new StringBuilder(8192);
            data.Append("{\"placements\":[");
            int successes = 0;
            for (int i = 0; i < placements.Count; i++)
            {
                RimGPTBlueprintPlacement placement = placements[i];
                BlueprintPlacementResult result = TryPlaceBlueprint(placement.BuildDef, placement.X, placement.Z, placement.Rotation, placement.StuffDef);
                if (result.Success)
                {
                    successes++;
                }

                if (i > 0)
                {
                    data.Append(",");
                }

                data.Append("{");
                data.Append("\"index\":").Append(i.ToString(CultureInfo.InvariantCulture));
                data.Append(",\"success\":").Append(result.Success ? "true" : "false");
                data.Append(",\"message\":\"").Append(RimGPTJson.Escape(result.Message)).Append("\"");
                if (!string.IsNullOrEmpty(result.BlueprintId))
                {
                    data.Append(",\"blueprintId\":\"").Append(RimGPTJson.Escape(result.BlueprintId)).Append("\"");
                }
                data.Append("}");
            }
            data.Append("]}");

            return RimGPTCommandExecutionResult.Succeeded("Placed " + successes + " of " + placements.Count + " blueprint(s)", data.ToString());
        }

        private static RimGPTCommandExecutionResult CancelAt(int x, int z)
        {
            Map map;
            IntVec3 cell;
            RimGPTCommandExecutionResult cellResult = TryGetVisibleCell(x, z, out map, out cell);
            if (!cellResult.Success)
            {
                return cellResult;
            }

            Designator_Cancel designator = new Designator_Cancel();
            AcceptanceReport report = designator.CanDesignateCell(cell);
            if (!report)
            {
                return RimGPTCommandExecutionResult.Failure(SafeReportReason(report, "Nothing cancellable at this cell"));
            }

            designator.DesignateSingleCell(cell);
            return RimGPTCommandExecutionResult.Succeeded("Cancelable orders removed at cell");
        }

        private static RimGPTCommandExecutionResult DesignateDeconstruct(string thingId)
        {
            Thing thing;
            RimGPTCommandExecutionResult lookup = TryGetVisibleThing(thingId, out thing);
            if (!lookup.Success)
            {
                return lookup;
            }

            if (!(thing is Building) && !(thing is Frame))
            {
                return RimGPTCommandExecutionResult.Failure("Thing is not a deconstructable structure");
            }

            if (thing.Faction != Faction.OfPlayer)
            {
                return RimGPTCommandExecutionResult.Failure("Only player structures can be deconstructed through RimGPT");
            }

            Map map = Find.CurrentMap;
            if (map.designationManager.DesignationOn(thing, DesignationDefOf.Deconstruct) != null)
            {
                return RimGPTCommandExecutionResult.Succeeded("Deconstruct designation already exists");
            }

            Designator_Deconstruct designator = new Designator_Deconstruct();
            AcceptanceReport report = designator.CanDesignateThing(thing);
            if (!report)
            {
                return RimGPTCommandExecutionResult.Failure(SafeReportReason(report, "Deconstruct designation is not allowed for this target"));
            }

            designator.DesignateThing(thing);
            return RimGPTCommandExecutionResult.Succeeded("Deconstruct designation added");
        }

        private static int AddValidZoneCells(Zone zone, Map map, int minX, int minZ, int maxX, int maxZ, bool growing)
        {
            NormalizeRect(ref minX, ref minZ, ref maxX, ref maxZ);
            int added = 0;
            for (int x = minX; x <= maxX; x++)
            {
                for (int z = minZ; z <= maxZ; z++)
                {
                    IntVec3 cell = new IntVec3(x, 0, z);
                    if (!cell.InBounds(map) || cell.Fogged(map) || map.zoneManager.ZoneAt(cell) != null)
                    {
                        continue;
                    }

                    if (growing)
                    {
                        ThingDef rice = DefDatabase<ThingDef>.GetNamedSilentFail("Plant_Rice");
                        if (rice == null || !PlantUtility.CanNowPlantAt(rice, cell, map, false))
                        {
                            continue;
                        }
                    }
                    else if (!cell.Walkable(map))
                    {
                        continue;
                    }

                    zone.AddCell(cell);
                    added++;
                }
            }

            return added;
        }

        private static void NormalizeRect(ref int minX, ref int minZ, ref int maxX, ref int maxZ)
        {
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
        }

        private static bool TryParseStoragePriority(string priorityName, out StoragePriority priority)
        {
            priority = StoragePriority.Normal;
            if (string.IsNullOrEmpty(priorityName))
            {
                return false;
            }

            return System.Enum.TryParse(priorityName, true, out priority)
                && priority != StoragePriority.Unstored;
        }

        private static string StoragePresetCategory(string preset)
        {
            if (preset.Equals("food", System.StringComparison.OrdinalIgnoreCase))
            {
                return "Foods";
            }
            if (preset.Equals("rawResources", System.StringComparison.OrdinalIgnoreCase))
            {
                return "ResourcesRaw";
            }
            if (preset.Equals("manufactured", System.StringComparison.OrdinalIgnoreCase))
            {
                return "Manufactured";
            }
            if (preset.Equals("weapons", System.StringComparison.OrdinalIgnoreCase))
            {
                return "Weapons";
            }
            if (preset.Equals("apparel", System.StringComparison.OrdinalIgnoreCase))
            {
                return "Apparel";
            }
            if (preset.Equals("chunks", System.StringComparison.OrdinalIgnoreCase))
            {
                return "Chunks";
            }
            if (preset.Equals("corpses", System.StringComparison.OrdinalIgnoreCase))
            {
                return "Corpses";
            }

            return preset;
        }

        private static BlueprintPlacementResult TryPlaceBlueprint(string buildDefName, int x, int z, string rotationName, string stuffDefName)
        {
            Map map;
            IntVec3 cell;
            RimGPTCommandExecutionResult cellResult = TryGetVisibleCell(x, z, out map, out cell);
            if (!cellResult.Success)
            {
                return BlueprintPlacementResult.Failed(cellResult.Message);
            }

            BuildableDef buildable = RimGPTCatalogJson.FindBuildable(buildDefName);
            if (buildable == null)
            {
                return BlueprintPlacementResult.Failed("Unknown buildDef");
            }

            if (!RimGPTCatalogJson.PlayerCanBuild(buildable))
            {
                return BlueprintPlacementResult.Failed("Build option is not currently available to the player");
            }

            Rot4 rotation;
            if (!TryParseRotation(rotationName, out rotation))
            {
                return BlueprintPlacementResult.Failed("Unknown rotation");
            }

            string stuffError;
            ThingDef stuff = ResolveStuff(buildable, stuffDefName, out stuffError);
            if (stuffError != null)
            {
                return BlueprintPlacementResult.Failed(stuffError);
            }

            AcceptanceReport report = GenConstruct.CanPlaceBlueprintAt(buildable, cell, rotation, map, false, null, null, stuff, false, false, false);
            if (!report)
            {
                return BlueprintPlacementResult.Failed(SafeReportReason(report, "Blueprint cannot be placed at this cell"));
            }

            Blueprint_Build blueprint = GenConstruct.PlaceBlueprintForBuild(buildable, cell, map, rotation, Faction.OfPlayer, stuff, null, null, true);
            string blueprintId = blueprint != null ? blueprint.ThingID : null;
            string data = "{\"blueprintId\":\"" + RimGPTJson.Escape(blueprintId) + "\"}";
            return BlueprintPlacementResult.Succeeded("Blueprint placed", blueprintId, data);
        }

        private static bool TryParseRotation(string rotationName, out Rot4 rotation)
        {
            string value = string.IsNullOrEmpty(rotationName) ? "North" : rotationName;
            if (value.Equals("North", System.StringComparison.OrdinalIgnoreCase))
            {
                rotation = Rot4.North;
                return true;
            }
            if (value.Equals("East", System.StringComparison.OrdinalIgnoreCase))
            {
                rotation = Rot4.East;
                return true;
            }
            if (value.Equals("South", System.StringComparison.OrdinalIgnoreCase))
            {
                rotation = Rot4.South;
                return true;
            }
            if (value.Equals("West", System.StringComparison.OrdinalIgnoreCase))
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

        private static RimGPTCommandExecutionResult TryGetZone(string zoneId, out Zone zone)
        {
            zone = null;
            if (string.IsNullOrEmpty(zoneId))
            {
                return RimGPTCommandExecutionResult.Failure("Missing zoneId");
            }

            Map map;
            RimGPTCommandExecutionResult mapResult = TryGetCurrentMap(out map);
            if (!mapResult.Success)
            {
                return mapResult;
            }

            List<Zone> zones = map.zoneManager.AllZones;
            for (int i = 0; i < zones.Count; i++)
            {
                Zone candidate = zones[i];
                if (candidate == null)
                {
                    continue;
                }

                string candidateId = RimGPTSpatialJson.ZoneId(candidate);
                if (zoneId == candidateId || zoneId == candidate.ID.ToString(CultureInfo.InvariantCulture) || zoneId == candidate.GetUniqueLoadID())
                {
                    zone = candidate;
                    return RimGPTCommandExecutionResult.Succeeded("Zone found");
                }
            }

            return RimGPTCommandExecutionResult.Failure("Zone was not found on the current player map");
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

        private sealed class BlueprintPlacementResult
        {
            public bool Success;
            public string Message;
            public string BlueprintId;
            public string DataJson;

            public static BlueprintPlacementResult Succeeded(string message, string blueprintId, string dataJson)
            {
                return new BlueprintPlacementResult
                {
                    Success = true,
                    Message = message,
                    BlueprintId = blueprintId,
                    DataJson = dataJson
                };
            }

            public static BlueprintPlacementResult Failed(string message)
            {
                return new BlueprintPlacementResult
                {
                    Success = false,
                    Message = message
                };
            }
        }
    }

    public sealed class RimGPTCommandExecutionResult
    {
        public bool Success;
        public string Message;
        public string DataJson;

        public static RimGPTCommandExecutionResult Succeeded(string message, string dataJson = null)
        {
            return new RimGPTCommandExecutionResult
            {
                Success = true,
                Message = message,
                DataJson = dataJson
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
