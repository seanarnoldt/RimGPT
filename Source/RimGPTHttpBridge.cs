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
            if (!TryCreateCommand(commandName, out command))
            {
                WriteJson(context.Response, 400, "{\"error\":\"unsupportedCommand\",\"command\":\"" + RimGPTJson.Escape(commandName) + "\"}");
                return;
            }

            RimGPTCommandQueue.Enqueue(command);
            WriteJson(context.Response, 202, "{\"accepted\":true,\"command\":\"" + command.CommandName + "\"}");
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

        private static bool TryCreateCommand(string commandName, out RimGPTCommand command)
        {
            command = null;

            if (string.Equals(commandName, "pause", StringComparison.OrdinalIgnoreCase))
            {
                command = new RimGPTCommand(RimGPTCommandType.Pause);
                return true;
            }

            if (string.Equals(commandName, "unpause", StringComparison.OrdinalIgnoreCase))
            {
                command = new RimGPTCommand(RimGPTCommandType.Unpause);
                return true;
            }

            return false;
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
