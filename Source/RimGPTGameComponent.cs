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

        public override void GameComponentUpdate()
        {
            ProcessQueuedCommands();

            int now = Environment.TickCount;
            if (now >= nextSnapshotUpdateMillis)
            {
                UpdateStateSnapshot();
                nextSnapshotUpdateMillis = now + 1000;
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
                    RimGPTCommandExecutionResult result = RimGPTCommandExecutor.Execute(command);
                    RimGPTCommandQueue.Complete(command, result.Success, result.Message);
                    Log.Message("[RimGPT] Executed command: " + command.CommandName + " (" + command.CommandId + "): " + (result.Success ? "success" : "failure"));
                }
                catch (Exception ex)
                {
                    RimGPTCommandQueue.Complete(command, false, "Exception while executing command");
                    Log.Error("[RimGPT] Exception executing command '" + command.CommandName + "' (" + command.CommandId + "): " + ex);
                }
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
