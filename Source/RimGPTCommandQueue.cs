using System.Collections.Concurrent;
using Verse;

namespace RimGPT
{
    public static class RimGPTCommandQueue
    {
        private static readonly ConcurrentQueue<RimGPTCommand> Commands = new ConcurrentQueue<RimGPTCommand>();

        public static void Enqueue(RimGPTCommand command)
        {
            Commands.Enqueue(command);
            Log.Message("[RimGPT] Queued command: " + command.CommandName);
        }

        public static bool TryDequeue(out RimGPTCommand command)
        {
            return Commands.TryDequeue(out command);
        }
    }
}
