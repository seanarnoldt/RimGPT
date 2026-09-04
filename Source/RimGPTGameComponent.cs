using System;
using RimWorld;
using Verse;

namespace RimGPT
{
    public sealed class RimGPTGameComponent : GameComponent
    {
        private int nextSnapshotUpdateMillis;

        public RimGPTGameComponent(Game game)
        {
        }

        public override void FinalizeInit()
        {
            base.FinalizeInit();
            UpdateStateSnapshot();
            Log.Message("[RimGPT] Game component initialized");
        }

        public override void GameComponentTick()
        {
            if (Find.TickManager.TicksGame % 30 == 0)
            {
                UpdateStateSnapshot();
            }
        }

        public override void GameComponentUpdate()
        {
            ProcessQueuedCommands();

            int now = Environment.TickCount;
            if (now >= nextSnapshotUpdateMillis)
            {
                UpdateStateSnapshot();
                nextSnapshotUpdateMillis = now + 500;
            }
        }

        public override void LoadedGame()
        {
            base.LoadedGame();
            UpdateStateSnapshot();
        }

        public override void StartedNewGame()
        {
            base.StartedNewGame();
            UpdateStateSnapshot();
        }

        private static void ProcessQueuedCommands()
        {
            RimGPTCommand command;
            while (RimGPTCommandQueue.TryDequeue(out command))
            {
                try
                {
                    ExecuteCommand(command);
                    Log.Message("[RimGPT] Executed command: " + command.CommandName);
                }
                catch (Exception ex)
                {
                    Log.Error("[RimGPT] Exception executing command '" + command.CommandName + "': " + ex);
                }
            }
        }

        private static void ExecuteCommand(RimGPTCommand command)
        {
            if (Find.TickManager == null)
            {
                Log.Warning("[RimGPT] Ignoring command because TickManager is not available: " + command.CommandName);
                return;
            }

            switch (command.Type)
            {
                case RimGPTCommandType.Pause:
                    Find.TickManager.CurTimeSpeed = TimeSpeed.Paused;
                    break;
                case RimGPTCommandType.Unpause:
                    if (Find.TickManager.CurTimeSpeed == TimeSpeed.Paused)
                    {
                        Find.TickManager.CurTimeSpeed = TimeSpeed.Normal;
                    }
                    break;
                default:
                    throw new InvalidOperationException("Unsupported command type: " + command.Type);
            }
        }

        private static void UpdateStateSnapshot()
        {
            try
            {
                RimGPTStateSnapshot.UpdateFromGame();
            }
            catch (Exception ex)
            {
                Log.Error("[RimGPT] Exception updating state snapshot: " + ex);
            }
        }
    }
}
