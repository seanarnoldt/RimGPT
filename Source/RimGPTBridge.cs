using Verse;

namespace RimGPT
{
    [StaticConstructorOnStartup]
    public static class RimGPTBridge
    {
        static RimGPTBridge()
        {
            Log.Message("[RimGPT] Bridge loaded successfully");
            RimGPTHttpBridge.Start();
        }
    }
}
