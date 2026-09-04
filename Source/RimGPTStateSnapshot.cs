using Verse;

namespace RimGPT
{
    public static class RimGPTStateSnapshot
    {
        private static volatile string currentJson = "{\"schemaVersion\":1,\"game\":{\"loaded\":false}}";

        public static string CurrentJson
        {
            get { return currentJson; }
        }

        public static void UpdateFromGame()
        {
            currentJson = RimGPTStateBuilder.BuildJson();
        }
    }
}
