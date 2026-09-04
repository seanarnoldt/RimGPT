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

            error = "unsupportedCommand";
            return false;
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
