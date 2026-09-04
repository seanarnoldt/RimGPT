using System;
using RimWorld;
using Verse;

namespace RimGPT
{
    public sealed class RimGPTGameComponent : GameComponent
    {
        private const int MaxCommandsPerFrame = 32;
        private const int MaxReadRequestsPerFrame = 8;
        private int nextSnapshotUpdateMillis;
        private static bool dispatcherActiveLogged;

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
            bool commandsProcessed = ProcessQueuedCommands();
            if (commandsProcessed)
            {
                nextSnapshotUpdateMillis = Environment.TickCount + 1000;
            }

            ProcessReadRequests();

            int now = Environment.TickCount;
            if (now >= nextSnapshotUpdateMillis)
            {
                UpdateStateSnapshot();
                nextSnapshotUpdateMillis = now + 1000;
            }
        }

        private static void ProcessReadRequests()
        {
            int drained = 0;
            RimGPTReadRequest request;
            while (drained < MaxReadRequestsPerFrame && RimGPTReadRequestQueue.TryDequeue(out request))
            {
                drained++;
                try
                {
                    string json;
                    switch (request.Type)
                    {
                        case RimGPTReadRequestType.MapRegion:
                            json = RimGPTSpatialJson.BuildMapRegionJson(request.MinX, request.MinZ, request.MaxX, request.MaxZ);
                            break;
                        case RimGPTReadRequestType.BuildOptions:
                            json = RimGPTCatalogJson.BuildBuildOptionsJson(request.Category, request.Search);
                            break;
                        case RimGPTReadRequestType.BuildInfo:
                            json = RimGPTCatalogJson.BuildBuildInfoJson(request.DefName);
                            break;
                        case RimGPTReadRequestType.GrowablePlants:
                            json = RimGPTCatalogJson.BuildGrowablePlantsJson();
                            break;
                        case RimGPTReadRequestType.CheckBuildPlacements:
                            json = RimGPTCatalogJson.BuildCheckBuildPlacementsJson(request.Placements);
                            break;
                        default:
                            RimGPTReadRequestQueue.CompleteFailure(request, "unsupportedReadRequest");
                            continue;
                    }

                    RimGPTReadRequestQueue.CompleteSuccess(request, json);
                }
                catch (Exception ex)
                {
                    RimGPTReadRequestQueue.CompleteFailure(request, "readRequestFailed");
                    Log.Error("[RimGPT] Exception processing read request '" + request.Type + "': " + ex);
                }
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

        private static bool ProcessQueuedCommands()
        {
            if (!dispatcherActiveLogged)
            {
                dispatcherActiveLogged = true;
                Log.Message("[RimGPT] Main-thread dispatcher active");
            }

            int drained = 0;
            System.Collections.Generic.List<CommandCompletion> completions = new System.Collections.Generic.List<CommandCompletion>();
            RimGPTCommand command;
            while (drained < MaxCommandsPerFrame && RimGPTCommandQueue.TryDequeue(out command))
            {
                drained++;
                try
                {
                    int queuedForMillis = Environment.TickCount - command.QueuedAtMillis;
                    Log.Message("[RimGPT] Executing command " + command.CommandId + ", queued for " + queuedForMillis + " ms");
                    RimGPTCommandExecutionResult result = RimGPTCommandExecutor.Execute(command);
                    completions.Add(new CommandCompletion(command, result.Success, result.Message, result.DataJson));
                }
                catch (Exception ex)
                {
                    completions.Add(new CommandCompletion(command, false, "Exception while executing command", null));
                    Log.Error("[RimGPT] Exception executing command '" + command.CommandName + "' (" + command.CommandId + "): " + ex);
                }
            }

            if (drained > 0)
            {
                UpdateStateSnapshot();
                for (int i = 0; i < completions.Count; i++)
                {
                    CommandCompletion completion = completions[i];
                    RimGPTCommandQueue.Complete(completion.Command, completion.Success, completion.Message, completion.DataJson);
                    Log.Message("[RimGPT] Executed command: " + completion.Command.CommandName + " (" + completion.Command.CommandId + "): " + (completion.Success ? "success" : "failure"));
                }

                Log.Message("[RimGPT] Drained " + drained + " command(s) this frame");
            }

            return drained > 0;
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

        private sealed class CommandCompletion
        {
            public RimGPTCommand Command;
            public bool Success;
            public string Message;
            public string DataJson;

            public CommandCompletion(RimGPTCommand command, bool success, string message, string dataJson)
            {
                Command = command;
                Success = success;
                Message = message;
                DataJson = dataJson;
            }
        }
    }
}
