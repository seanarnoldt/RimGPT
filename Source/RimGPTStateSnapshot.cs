using Verse;
using System;
using System.Threading;

namespace RimGPT
{
    public static class RimGPTStateSnapshot
    {
        private static readonly object SnapshotLock = new object();
        private static string currentJson = "{\"schemaVersion\":2,\"snapshot\":{\"version\":0,\"capturedAtUtc\":null,\"ticksGame\":0},\"game\":{\"loaded\":false}}";
        private static long currentVersion;

        public static string CurrentJson
        {
            get
            {
                lock (SnapshotLock)
                {
                    return currentJson;
                }
            }
        }

        public static long CurrentVersion
        {
            get
            {
                lock (SnapshotLock)
                {
                    return currentVersion;
                }
            }
        }

        public static void UpdateFromGame()
        {
            lock (SnapshotLock)
            {
                currentVersion++;
                string capturedAtUtc = DateTime.UtcNow.ToString("o");
                int ticksGame = 0;
                try
                {
                    ticksGame = Find.TickManager != null ? Find.TickManager.TicksGame : 0;
                }
                catch
                {
                    ticksGame = 0;
                }

                currentJson = RimGPTStateBuilder.BuildJson(currentVersion, capturedAtUtc, ticksGame);
                Monitor.PulseAll(SnapshotLock);
            }
        }

        public static bool TryWaitForNewerJson(long afterVersion, int timeoutMillis, out string json)
        {
            int boundedTimeout = Math.Max(0, Math.Min(timeoutMillis, 10000));
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(boundedTimeout);

            lock (SnapshotLock)
            {
                while (currentVersion <= afterVersion)
                {
                    TimeSpan remaining = deadline - DateTime.UtcNow;
                    if (remaining <= TimeSpan.Zero)
                    {
                        json = currentJson;
                        return false;
                    }

                    Monitor.Wait(SnapshotLock, remaining);
                }

                json = currentJson;
                return true;
            }
        }
    }
}
