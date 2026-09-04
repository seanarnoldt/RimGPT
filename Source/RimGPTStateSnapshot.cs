using System.Collections.Generic;
using System.Text;
using RimWorld;
using Verse;

namespace RimGPT
{
    public static class RimGPTStateSnapshot
    {
        private static volatile string currentJson = "{\"gameLoaded\":false}";

        public static string CurrentJson
        {
            get { return currentJson; }
        }

        public static void UpdateFromGame()
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                currentJson = "{\"gameLoaded\":false}";
                return;
            }

            Map map = Find.CurrentMap;
            List<Pawn> colonists = map.mapPawns.FreeColonistsSpawned;

            StringBuilder json = new StringBuilder();
            json.Append("{\"gameLoaded\":true");
            json.Append(",\"paused\":");
            json.Append(Find.TickManager.Paused ? "true" : "false");
            json.Append(",\"speed\":");
            json.Append((int)Find.TickManager.CurTimeSpeed);
            json.Append(",\"map\":{\"colonistCount\":");
            json.Append(colonists.Count);
            json.Append("},\"colonists\":[");

            for (int i = 0; i < colonists.Count; i++)
            {
                Pawn pawn = colonists[i];
                if (i > 0)
                {
                    json.Append(",");
                }

                IntVec3 position = pawn.Position;
                json.Append("{\"name\":\"");
                json.Append(RimGPTJson.Escape(pawn.LabelShortCap));
                json.Append("\",\"position\":{\"x\":");
                json.Append(position.x);
                json.Append(",\"z\":");
                json.Append(position.z);
                json.Append("},\"drafted\":");
                json.Append(pawn.Drafted ? "true" : "false");
                json.Append("}");
            }

            json.Append("]}");
            currentJson = json.ToString();
        }
    }
}
