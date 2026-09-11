using System;
using System.Collections.Concurrent;
using System.Threading;
using Verse;

namespace RimGPT
{
    public enum RimGPTReadRequestType
    {
        MapRegion,
        RoomAt,
        ConstructionDiagnostic,
        ResourceSources,
        BuildOptions,
        BuildInfo,
        GrowablePlants,
        CheckBuildPlacements,
        CheckZonePlacement,
        Recipes
    }

    public sealed class RimGPTReadRequest
    {
        public RimGPTReadRequestType Type;
        public int MinX;
        public int MinZ;
        public int MaxX;
        public int MaxZ;
        public string Category;
        public string Search;
        public string DefName;
        public string WorktableId;
        public string ThingId;
        public string ResourceDef;
        public string ZoneType;
        public System.Collections.Generic.List<RimGPTBlueprintPlacement> Placements;
        public int QueuedAtMillis;
        public string Json;
        public string Error;
        public ManualResetEventSlim Completed = new ManualResetEventSlim(false);
    }

    public static class RimGPTReadRequestQueue
    {
        private const int DefaultTimeoutMillis = 3000;
        private static readonly ConcurrentQueue<RimGPTReadRequest> Requests = new ConcurrentQueue<RimGPTReadRequest>();

        public static RimGPTReadRequest Enqueue(RimGPTReadRequest request)
        {
            request.QueuedAtMillis = Environment.TickCount;
            Requests.Enqueue(request);
            return request;
        }

        public static bool TryDequeue(out RimGPTReadRequest request)
        {
            return Requests.TryDequeue(out request);
        }

        public static string EnqueueAndWait(RimGPTReadRequest request, out int statusCode)
        {
            Enqueue(request);
            if (!request.Completed.Wait(DefaultTimeoutMillis))
            {
                statusCode = 503;
                return "{\"error\":\"mainThreadReadTimedOut\"}";
            }

            if (!string.IsNullOrEmpty(request.Error))
            {
                statusCode = 400;
                return "{\"error\":\"" + RimGPTJson.Escape(request.Error) + "\"}";
            }

            statusCode = 200;
            return request.Json ?? "{}";
        }

        public static void CompleteSuccess(RimGPTReadRequest request, string json)
        {
            request.Json = json;
            request.Completed.Set();
        }

        public static void CompleteFailure(RimGPTReadRequest request, string error)
        {
            request.Error = error;
            request.Completed.Set();
        }
    }
}
