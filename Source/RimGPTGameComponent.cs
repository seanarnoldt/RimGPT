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
            ProcessQueuedCommands();
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

        private static void ProcessQueuedCommands()
        {
            if (!dispatcherActiveLogged)
            {
                dispatcherActiveLogged = true;
                Log.Message("[RimGPT] Main-thread dispatcher active");
            }

            int drained = 0;
            RimGPTCommand command;
            while (drained < MaxCommandsPerFrame && RimGPTCommandQueue.TryDequeue(out command))
            {
                drained++;
                try
                {
                    int queuedForMillis = Environment.TickCount - command.QueuedAtMillis;
                    Log.Message("[RimGPT] Executing command " + command.CommandId + ", queued for " + queuedForMillis + " ms");
                    RimGPTCommandExecutionResult result = RimGPTCommandExecutor.Execute(command);
                    RimGPTCommandQueue.Complete(command, result.Success, result.Message, result.DataJson);
                    Log.Message("[RimGPT] Executed command: " + command.CommandName + " (" + command.CommandId + "): " + (result.Success ? "success" : "failure"));
                }
                catch (Exception ex)
                {
                    RimGPTCommandQueue.Complete(command, false, "Exception while executing command");
                    Log.Error("[RimGPT] Exception executing command '" + command.CommandName + "' (" + command.CommandId + "): " + ex);
                }
            }

            if (drained > 0)
            {
                Log.Message("[RimGPT] Drained " + drained + " command(s) this frame");
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
