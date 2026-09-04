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
