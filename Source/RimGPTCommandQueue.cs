using System.Collections.Concurrent;
using System.Collections.Generic;
using System;
using Verse;

namespace RimGPT
{
    public static class RimGPTCommandQueue
    {
        private const int MaxTrackedCommands = 200;
        private static readonly ConcurrentQueue<RimGPTCommand> Commands = new ConcurrentQueue<RimGPTCommand>();
        private static readonly object ResultsLock = new object();
        private static readonly Dictionary<string, RimGPTCommandResult> Results = new Dictionary<string, RimGPTCommandResult>();
        private static readonly Queue<string> ResultOrder = new Queue<string>();

        public static void Enqueue(RimGPTCommand command)
        {
            command.QueuedAtMillis = Environment.TickCount;

            lock (ResultsLock)
            {
                Results[command.CommandId] = RimGPTCommandResult.Queued(command.CommandId);
                ResultOrder.Enqueue(command.CommandId);
                TrimResultsLocked();
            }

            Commands.Enqueue(command);
            Log.Message("[RimGPT] Queued command: " + command.CommandName + " (" + command.CommandId + ")");
        }

        public static bool TryDequeue(out RimGPTCommand command)
        {
            return Commands.TryDequeue(out command);
        }

        public static void Complete(RimGPTCommand command, bool success, string messageOrError, string dataJson = null)
        {
            RimGPTCommandResult result = success
                ? RimGPTCommandResult.CompletedSuccess(command.CommandId, messageOrError, dataJson)
                : RimGPTCommandResult.CompletedFailure(command.CommandId, messageOrError);

            lock (ResultsLock)
            {
                if (!Results.ContainsKey(command.CommandId))
                {
                    ResultOrder.Enqueue(command.CommandId);
                }

                Results[command.CommandId] = result;
                TrimResultsLocked();
            }
        }

        public static bool TryGetResultJson(string commandId, out string json)
        {
            lock (ResultsLock)
            {
                RimGPTCommandResult result;
                if (!Results.TryGetValue(commandId, out result))
                {
                    json = null;
                    return false;
                }

                json = result.ToJson();
                return true;
            }
        }

        private static void TrimResultsLocked()
        {
            while (ResultOrder.Count > MaxTrackedCommands)
            {
                string oldest = ResultOrder.Dequeue();
                Results.Remove(oldest);
            }
        }
    }

    public sealed class RimGPTCommandResult
    {
        public string CommandId;
        public string Status;
        public bool Success;
        public string Message;
        public string Error;
        public string DataJson;

        public static RimGPTCommandResult Queued(string commandId)
        {
            return new RimGPTCommandResult
            {
                CommandId = commandId,
                Status = "queued"
            };
        }

        public static RimGPTCommandResult CompletedSuccess(string commandId, string message, string dataJson)
        {
            return new RimGPTCommandResult
            {
                CommandId = commandId,
                Status = "completed",
                Success = true,
                Message = message,
                DataJson = dataJson
            };
        }

        public static RimGPTCommandResult CompletedFailure(string commandId, string error)
        {
            return new RimGPTCommandResult
            {
                CommandId = commandId,
                Status = "completed",
                Success = false,
                Error = error
            };
        }

        public string ToJson()
        {
            if (Status == "queued")
            {
                return "{\"commandId\":\"" + RimGPTJson.Escape(CommandId) + "\",\"status\":\"queued\"}";
            }

            if (Success)
            {
                string json = "{\"commandId\":\"" + RimGPTJson.Escape(CommandId) + "\",\"status\":\"completed\",\"success\":true,\"message\":\"" + RimGPTJson.Escape(Message) + "\"";
                if (!string.IsNullOrEmpty(DataJson))
                {
                    json += ",\"data\":" + DataJson;
                }

                return json + "}";
            }

            return "{\"commandId\":\"" + RimGPTJson.Escape(CommandId) + "\",\"status\":\"completed\",\"success\":false,\"error\":\"" + RimGPTJson.Escape(Error) + "\"}";
        }
    }
}
