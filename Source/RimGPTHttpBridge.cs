using System;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using Verse;

namespace RimGPT
{
    public static class RimGPTHttpBridge
    {
        private const string Prefix = "http://127.0.0.1:47831/";
        private static readonly object LifecycleLock = new object();
        private static HttpListener listener;
        private static Thread listenerThread;
        private static volatile bool running;

        public static void Start()
        {
            lock (LifecycleLock)
            {
                if (running)
                {
                    return;
                }

                try
                {
                    listener = new HttpListener();
                    listener.Prefixes.Add(Prefix);
                    listener.Start();
                    running = true;

                    listenerThread = new Thread(ListenLoop);
                    listenerThread.Name = "RimGPT HTTP Bridge";
                    listenerThread.IsBackground = true;
                    listenerThread.Start();

                    AppDomain.CurrentDomain.DomainUnload += HandleShutdown;
                    AppDomain.CurrentDomain.ProcessExit += HandleShutdown;

                    Log.Message("[RimGPT] HTTP bridge started on " + Prefix);
                }
                catch (Exception ex)
                {
                    running = false;
                    listener = null;
                    Log.Error("[RimGPT] Exception starting HTTP bridge: " + ex);
                }
            }
        }

        public static void Stop()
        {
            lock (LifecycleLock)
            {
                if (!running && listener == null)
                {
                    return;
                }

                running = false;

                try
                {
                    if (listener != null)
                    {
                        listener.Stop();
                        listener.Close();
                    }
                }
                catch (Exception ex)
                {
                    Log.Error("[RimGPT] Exception stopping HTTP bridge: " + ex);
                }
                finally
                {
                    listener = null;
                    listenerThread = null;
                    Log.Message("[RimGPT] HTTP bridge stopped");
                }
            }
        }

        private static void HandleShutdown(object sender, EventArgs args)
        {
            Stop();
        }

        private static void ListenLoop()
        {
            while (running)
            {
                try
                {
                    HttpListenerContext context = listener.GetContext();
                    ThreadPool.QueueUserWorkItem(HandleRequest, context);
                }
                catch (HttpListenerException)
                {
                    if (running)
                    {
                        Log.Warning("[RimGPT] HTTP listener interrupted while running");
                    }
                }
                catch (ObjectDisposedException)
                {
                    return;
                }
                catch (Exception ex)
                {
                    if (running)
                    {
                        Log.Error("[RimGPT] Exception in HTTP listen loop: " + ex);
                    }
                }
            }
        }

        private static void HandleRequest(object state)
        {
            HttpListenerContext context = (HttpListenerContext)state;

            try
            {
                string method = context.Request.HttpMethod;
                string path = context.Request.Url.AbsolutePath;
                Log.Message("[RimGPT] Request: " + method + " " + path);

                if (method == "GET" && path == "/health")
                {
                    WriteJson(context.Response, 200, "{\"status\":\"ok\",\"bridge\":\"RimGPT\"}");
                    return;
                }

                if (method == "GET" && path == "/state")
                {
                    WriteJson(context.Response, 200, RimGPTStateSnapshot.CurrentJson);
                    return;
                }

                if (method == "GET" && path == "/map/region")
                {
                    HandleMapRegion(context);
                    return;
                }

                if (method == "GET" && path == "/build/options")
                {
                    HandleBuildOptions(context);
                    return;
                }

                if (method == "GET" && path == "/build/info")
                {
                    HandleBuildInfo(context);
                    return;
                }

                if (method == "GET" && path == "/growable-plants")
                {
                    HandleGrowablePlants(context);
                    return;
                }

                if (method == "POST" && path == "/command")
                {
                    HandleCommand(context);
                    return;
                }

                if (method == "GET" && path.StartsWith("/command/", StringComparison.Ordinal))
                {
                    HandleCommandStatus(context, path);
                    return;
                }

                WriteJson(context.Response, 404, "{\"error\":\"notFound\"}");
            }
            catch (Exception ex)
            {
                Log.Error("[RimGPT] Exception handling HTTP request: " + ex);

                try
                {
                    WriteJson(context.Response, 500, "{\"error\":\"internalServerError\"}");
                }
                catch
                {
                }
            }
        }

        private static void HandleCommand(HttpListenerContext context)
        {
            string body;
            using (StreamReader reader = new StreamReader(context.Request.InputStream, context.Request.ContentEncoding))
            {
                body = reader.ReadToEnd();
            }

            string commandName;
            if (!TryReadCommandName(body, out commandName))
            {
                WriteJson(context.Response, 400, "{\"error\":\"missingCommand\"}");
                return;
            }

            RimGPTCommand command;
            string error;
            if (!TryCreateCommand(commandName, body, out command, out error))
            {
                WriteJson(context.Response, 400, "{\"error\":\"" + RimGPTJson.Escape(error) + "\",\"command\":\"" + RimGPTJson.Escape(commandName) + "\"}");
                return;
            }

            RimGPTCommandQueue.Enqueue(command);
            WriteJson(context.Response, 202, "{\"accepted\":true,\"commandId\":\"" + RimGPTJson.Escape(command.CommandId) + "\"}");
        }

        private static void HandleCommandStatus(HttpListenerContext context, string path)
        {
            string commandId = Uri.UnescapeDataString(path.Substring("/command/".Length));
            string json;
            if (!RimGPTCommandQueue.TryGetResultJson(commandId, out json))
            {
                WriteJson(context.Response, 404, "{\"error\":\"unknownCommandId\"}");
                return;
            }

            WriteJson(context.Response, 200, json);
        }

        private static void HandleMapRegion(HttpListenerContext context)
        {
            int minX;
            int minZ;
            int maxX;
            int maxZ;
            if (!TryReadQueryInt(context, "minX", out minX)
                || !TryReadQueryInt(context, "minZ", out minZ)
                || !TryReadQueryInt(context, "maxX", out maxX)
                || !TryReadQueryInt(context, "maxZ", out maxZ))
            {
                WriteJson(context.Response, 400, "{\"error\":\"missingRegionBounds\"}");
                return;
            }

            RimGPTReadRequest request = new RimGPTReadRequest
            {
                Type = RimGPTReadRequestType.MapRegion,
                MinX = minX,
                MinZ = minZ,
                MaxX = maxX,
                MaxZ = maxZ
            };
            int statusCode;
            string json = RimGPTReadRequestQueue.EnqueueAndWait(request, out statusCode);
            WriteJson(context.Response, statusCode, json);
        }

        private static void HandleBuildOptions(HttpListenerContext context)
        {
            RimGPTReadRequest request = new RimGPTReadRequest
            {
                Type = RimGPTReadRequestType.BuildOptions,
                Category = context.Request.QueryString["category"],
                Search = context.Request.QueryString["search"]
            };
            int statusCode;
            string json = RimGPTReadRequestQueue.EnqueueAndWait(request, out statusCode);
            WriteJson(context.Response, statusCode, json);
        }

        private static void HandleBuildInfo(HttpListenerContext context)
        {
            string defName = context.Request.QueryString["defName"];
            if (string.IsNullOrEmpty(defName))
            {
                WriteJson(context.Response, 400, "{\"error\":\"missingDefName\"}");
                return;
            }

            RimGPTReadRequest request = new RimGPTReadRequest
            {
                Type = RimGPTReadRequestType.BuildInfo,
                DefName = defName
            };
            int statusCode;
            string json = RimGPTReadRequestQueue.EnqueueAndWait(request, out statusCode);
            WriteJson(context.Response, statusCode, json);
        }

        private static void HandleGrowablePlants(HttpListenerContext context)
        {
            RimGPTReadRequest request = new RimGPTReadRequest
            {
                Type = RimGPTReadRequestType.GrowablePlants
            };
            int statusCode;
            string json = RimGPTReadRequestQueue.EnqueueAndWait(request, out statusCode);
            WriteJson(context.Response, statusCode, json);
        }

        private static bool TryReadCommandName(string body, out string commandName)
        {
            commandName = null;

            if (string.IsNullOrEmpty(body))
            {
                return false;
            }

            Match match = Regex.Match(body, "\"command\"\\s*:\\s*\"(?<command>[^\"\\\\]*(?:\\\\.[^\"\\\\]*)*)\"");
            if (!match.Success)
            {
                return false;
            }

            commandName = Regex.Unescape(match.Groups["command"].Value);
            return true;
        }

        private static bool TryCreateCommand(string commandName, string body, out RimGPTCommand command, out string error)
        {
            command = null;
            error = null;
            string commandId;
            if (!TryReadOptionalCommandId(body, out commandId))
            {
                commandId = Guid.NewGuid().ToString("N");
            }

            if (string.Equals(commandName, "pause", StringComparison.OrdinalIgnoreCase))
            {
                command = new RimGPTCommand(commandId, RimGPTCommandType.Pause);
                return true;
            }

            if (string.Equals(commandName, "unpause", StringComparison.OrdinalIgnoreCase))
            {
                command = new RimGPTCommand(commandId, RimGPTCommandType.Unpause);
                return true;
            }

            if (string.Equals(commandName, "setSpeed", StringComparison.OrdinalIgnoreCase))
            {
                int speed;
                if (!TryReadInt(body, "speed", out speed))
                {
                    error = "missingSpeed";
                    return false;
                }

                if (speed < 0 || speed > 3)
                {
                    error = "invalidSpeed";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.SetSpeed);
                command.Speed = speed;
                return true;
            }

            if (string.Equals(commandName, "draft", StringComparison.OrdinalIgnoreCase))
            {
                string pawnId;
                if (!TryReadString(body, "pawnId", out pawnId))
                {
                    error = "missingPawnId";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.Draft);
                command.PawnId = pawnId;
                return true;
            }

            if (string.Equals(commandName, "undraft", StringComparison.OrdinalIgnoreCase))
            {
                string pawnId;
                if (!TryReadString(body, "pawnId", out pawnId))
                {
                    error = "missingPawnId";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.Undraft);
                command.PawnId = pawnId;
                return true;
            }

            if (string.Equals(commandName, "move", StringComparison.OrdinalIgnoreCase))
            {
                string pawnId;
                int x;
                int z;
                if (!TryReadString(body, "pawnId", out pawnId))
                {
                    error = "missingPawnId";
                    return false;
                }

                if (!TryReadInt(body, "x", out x) || !TryReadInt(body, "z", out z))
                {
                    error = "missingDestination";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.Move);
                command.PawnId = pawnId;
                command.X = x;
                command.Z = z;
                return true;
            }

            if (string.Equals(commandName, "setWorkPriority", StringComparison.OrdinalIgnoreCase))
            {
                string pawnId;
                string workType;
                int priority;
                if (!TryReadString(body, "pawnId", out pawnId))
                {
                    error = "missingPawnId";
                    return false;
                }

                if (!TryReadString(body, "workType", out workType))
                {
                    error = "missingWorkType";
                    return false;
                }

                if (!TryReadInt(body, "priority", out priority))
                {
                    error = "missingPriority";
                    return false;
                }

                if (priority < 0 || priority > 4)
                {
                    error = "invalidPriority";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.SetWorkPriority);
                command.PawnId = pawnId;
                command.WorkType = workType;
                command.Priority = priority;
                return true;
            }

            if (string.Equals(commandName, "allow", StringComparison.OrdinalIgnoreCase))
            {
                string thingId;
                if (!TryReadString(body, "thingId", out thingId))
                {
                    error = "missingThingId";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.Allow);
                command.ThingId = thingId;
                return true;
            }

            if (string.Equals(commandName, "forbid", StringComparison.OrdinalIgnoreCase))
            {
                string thingId;
                if (!TryReadString(body, "thingId", out thingId))
                {
                    error = "missingThingId";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.Forbid);
                command.ThingId = thingId;
                return true;
            }

            if (string.Equals(commandName, "allowAll", StringComparison.OrdinalIgnoreCase))
            {
                command = new RimGPTCommand(commandId, RimGPTCommandType.AllowAll);
                return true;
            }

            if (string.Equals(commandName, "setResearch", StringComparison.OrdinalIgnoreCase))
            {
                string researchDef;
                if (!TryReadString(body, "researchDef", out researchDef))
                {
                    error = "missingResearchDef";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.SetResearch);
                command.ResearchDef = researchDef;
                return true;
            }

            if (string.Equals(commandName, "prioritizeJob", StringComparison.OrdinalIgnoreCase))
            {
                string pawnId;
                string targetId;
                if (!TryReadString(body, "pawnId", out pawnId))
                {
                    error = "missingPawnId";
                    return false;
                }

                if (!TryReadString(body, "targetId", out targetId))
                {
                    error = "missingTargetId";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.PrioritizeJob);
                command.PawnId = pawnId;
                command.TargetId = targetId;
                return true;
            }

            if (string.Equals(commandName, "designateMine", StringComparison.OrdinalIgnoreCase))
            {
                int x;
                int z;
                if (!TryReadInt(body, "x", out x) || !TryReadInt(body, "z", out z))
                {
                    error = "missingCell";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.DesignateMine);
                command.X = x;
                command.Z = z;
                return true;
            }

            if (string.Equals(commandName, "designateCut", StringComparison.OrdinalIgnoreCase))
            {
                int x;
                int z;
                if (!TryReadInt(body, "x", out x) || !TryReadInt(body, "z", out z))
                {
                    error = "missingCell";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.DesignateCut);
                command.X = x;
                command.Z = z;
                return true;
            }

            if (string.Equals(commandName, "designateHarvest", StringComparison.OrdinalIgnoreCase))
            {
                int x;
                int z;
                if (!TryReadInt(body, "x", out x) || !TryReadInt(body, "z", out z))
                {
                    error = "missingCell";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.DesignateHarvest);
                command.X = x;
                command.Z = z;
                return true;
            }

            if (string.Equals(commandName, "designateHunt", StringComparison.OrdinalIgnoreCase))
            {
                string thingId;
                if (!TryReadString(body, "thingId", out thingId))
                {
                    error = "missingThingId";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.DesignateHunt);
                command.ThingId = thingId;
                return true;
            }

            if (string.Equals(commandName, "createStockpile", StringComparison.OrdinalIgnoreCase))
            {
                int minX;
                int minZ;
                int maxX;
                int maxZ;
                if (!TryReadRect(body, out minX, out minZ, out maxX, out maxZ))
                {
                    error = "missingBounds";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.CreateStockpile);
                command.MinX = minX;
                command.MinZ = minZ;
                command.MaxX = maxX;
                command.MaxZ = maxZ;
                return true;
            }

            if (string.Equals(commandName, "setStockpilePriority", StringComparison.OrdinalIgnoreCase))
            {
                string zoneId;
                string priority;
                if (!TryReadString(body, "zoneId", out zoneId))
                {
                    error = "missingZoneId";
                    return false;
                }

                if (!TryReadString(body, "priority", out priority))
                {
                    error = "missingPriority";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.SetStockpilePriority);
                command.ZoneId = zoneId;
                command.StoragePriority = priority;
                return true;
            }

            if (string.Equals(commandName, "setStockpilePreset", StringComparison.OrdinalIgnoreCase))
            {
                string zoneId;
                string preset;
                if (!TryReadString(body, "zoneId", out zoneId))
                {
                    error = "missingZoneId";
                    return false;
                }

                if (!TryReadString(body, "preset", out preset))
                {
                    error = "missingPreset";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.SetStockpilePreset);
                command.ZoneId = zoneId;
                command.Preset = preset;
                return true;
            }

            if (string.Equals(commandName, "createGrowingZone", StringComparison.OrdinalIgnoreCase))
            {
                int minX;
                int minZ;
                int maxX;
                int maxZ;
                if (!TryReadRect(body, out minX, out minZ, out maxX, out maxZ))
                {
                    error = "missingBounds";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.CreateGrowingZone);
                command.MinX = minX;
                command.MinZ = minZ;
                command.MaxX = maxX;
                command.MaxZ = maxZ;
                return true;
            }

            if (string.Equals(commandName, "setGrowingZonePlant", StringComparison.OrdinalIgnoreCase))
            {
                string zoneId;
                string plantDef;
                if (!TryReadString(body, "zoneId", out zoneId))
                {
                    error = "missingZoneId";
                    return false;
                }

                if (!TryReadString(body, "plantDef", out plantDef))
                {
                    error = "missingPlantDef";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.SetGrowingZonePlant);
                command.ZoneId = zoneId;
                command.PlantDef = plantDef;
                return true;
            }

            if (string.Equals(commandName, "placeBlueprint", StringComparison.OrdinalIgnoreCase))
            {
                RimGPTBlueprintPlacement placement;
                if (!TryReadPlacement(body, out placement))
                {
                    error = "missingPlacement";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.PlaceBlueprint);
                command.BuildDef = placement.BuildDef;
                command.X = placement.X;
                command.Z = placement.Z;
                command.Rotation = placement.Rotation;
                command.StuffDef = placement.StuffDef;
                return true;
            }

            if (string.Equals(commandName, "placeBlueprints", StringComparison.OrdinalIgnoreCase))
            {
                System.Collections.Generic.List<RimGPTBlueprintPlacement> placements;
                if (!TryReadPlacements(body, out placements))
                {
                    error = "missingPlacements";
                    return false;
                }

                if (placements.Count > 100)
                {
                    error = "tooManyPlacements";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.PlaceBlueprints);
                command.Placements = placements;
                return true;
            }

            if (string.Equals(commandName, "cancelAt", StringComparison.OrdinalIgnoreCase))
            {
                int x;
                int z;
                if (!TryReadInt(body, "x", out x) || !TryReadInt(body, "z", out z))
                {
                    error = "missingCell";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.CancelAt);
                command.X = x;
                command.Z = z;
                return true;
            }

            if (string.Equals(commandName, "designateDeconstruct", StringComparison.OrdinalIgnoreCase))
            {
                string thingId;
                if (!TryReadString(body, "thingId", out thingId))
                {
                    error = "missingThingId";
                    return false;
                }

                command = new RimGPTCommand(commandId, RimGPTCommandType.DesignateDeconstruct);
                command.ThingId = thingId;
                return true;
            }

            error = "unsupportedCommand";
            return false;
        }

        private static bool TryReadRect(string body, out int minX, out int minZ, out int maxX, out int maxZ)
        {
            minX = 0;
            minZ = 0;
            maxX = 0;
            maxZ = 0;
            return TryReadInt(body, "minX", out minX)
                && TryReadInt(body, "minZ", out minZ)
                && TryReadInt(body, "maxX", out maxX)
                && TryReadInt(body, "maxZ", out maxZ);
        }

        private static bool TryReadPlacement(string body, out RimGPTBlueprintPlacement placement)
        {
            placement = null;
            string buildDef;
            int x;
            int z;
            if (!TryReadString(body, "buildDef", out buildDef)
                || !TryReadInt(body, "x", out x)
                || !TryReadInt(body, "z", out z))
            {
                return false;
            }

            string rotation;
            if (!TryReadString(body, "rotation", out rotation))
            {
                rotation = "North";
            }

            string stuffDef;
            TryReadString(body, "stuffDef", out stuffDef);

            placement = new RimGPTBlueprintPlacement
            {
                BuildDef = buildDef,
                X = x,
                Z = z,
                Rotation = rotation,
                StuffDef = stuffDef
            };
            return true;
        }

        private static bool TryReadPlacements(string body, out System.Collections.Generic.List<RimGPTBlueprintPlacement> placements)
        {
            placements = new System.Collections.Generic.List<RimGPTBlueprintPlacement>();
            Match arrayMatch = Regex.Match(body, "\"placements\"\\s*:\\s*\\[(?<items>.*)\\]", RegexOptions.Singleline);
            if (!arrayMatch.Success)
            {
                return false;
            }

            MatchCollection objectMatches = Regex.Matches(arrayMatch.Groups["items"].Value, "\\{[^{}]*\\}");
            for (int i = 0; i < objectMatches.Count; i++)
            {
                RimGPTBlueprintPlacement placement;
                if (!TryReadPlacement(objectMatches[i].Value, out placement))
                {
                    return false;
                }

                placements.Add(placement);
            }

            return placements.Count > 0;
        }

        private static bool TryReadString(string body, string fieldName, out string value)
        {
            value = null;

            if (string.IsNullOrEmpty(body))
            {
                return false;
            }

            Match match = Regex.Match(body, "\"" + Regex.Escape(fieldName) + "\"\\s*:\\s*\"(?<value>[^\"\\\\]*(?:\\\\.[^\"\\\\]*)*)\"");
            if (!match.Success)
            {
                return false;
            }

            value = Regex.Unescape(match.Groups["value"].Value);
            return !string.IsNullOrEmpty(value);
        }

        private static bool TryReadOptionalCommandId(string body, out string commandId)
        {
            commandId = null;

            string candidate;
            if (!TryReadString(body, "commandId", out candidate))
            {
                return false;
            }

            if (!Regex.IsMatch(candidate, "^[A-Za-z0-9_-]{1,80}$"))
            {
                return false;
            }

            commandId = candidate;
            return true;
        }

        private static bool TryReadInt(string body, string fieldName, out int value)
        {
            value = 0;

            if (string.IsNullOrEmpty(body))
            {
                return false;
            }

            Match match = Regex.Match(body, "\"" + Regex.Escape(fieldName) + "\"\\s*:\\s*(?<value>-?\\d+)");
            if (!match.Success)
            {
                return false;
            }

            return int.TryParse(match.Groups["value"].Value, out value);
        }

        private static bool TryReadQueryInt(HttpListenerContext context, string fieldName, out int value)
        {
            value = 0;
            string raw = context.Request.QueryString[fieldName];
            return !string.IsNullOrEmpty(raw) && int.TryParse(raw, out value);
        }

        private static void WriteJson(HttpListenerResponse response, int statusCode, string json)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(json);
            response.StatusCode = statusCode;
            response.ContentType = "application/json; charset=utf-8";
            response.ContentLength64 = bytes.Length;
            response.OutputStream.Write(bytes, 0, bytes.Length);
            response.OutputStream.Close();
        }
    }
}
