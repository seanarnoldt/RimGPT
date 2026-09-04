using System;
using System.Collections.Generic;
using RimWorld;
using Verse;

namespace RimGPT
{
    public enum RimGPTZonePlacementType
    {
        Growing,
        Stockpile
    }

    public sealed class RimGPTZoneCellResult
    {
        public IntVec3 Cell;
        public bool Valid;
        public string Reason;
    }

    public static class RimGPTZoneUtility
    {
        public static bool TryParseZoneType(string zoneType, out RimGPTZonePlacementType type)
        {
            type = RimGPTZonePlacementType.Growing;
            if (string.IsNullOrEmpty(zoneType))
            {
                return false;
            }

            if (zoneType.Equals("growing", StringComparison.OrdinalIgnoreCase))
            {
                type = RimGPTZonePlacementType.Growing;
                return true;
            }

            if (zoneType.Equals("stockpile", StringComparison.OrdinalIgnoreCase))
            {
                type = RimGPTZonePlacementType.Stockpile;
                return true;
            }

            return false;
        }

        public static void NormalizeRect(ref int minX, ref int minZ, ref int maxX, ref int maxZ)
        {
            if (minX > maxX)
            {
                int temp = minX;
                minX = maxX;
                maxX = temp;
            }

            if (minZ > maxZ)
            {
                int temp = minZ;
                minZ = maxZ;
                maxZ = temp;
            }
        }

        public static bool CanCreateZoneCell(Map map, IntVec3 cell, RimGPTZonePlacementType type, out string reason)
        {
            reason = null;
            if (map == null)
            {
                reason = "No current map";
                return false;
            }

            if (!cell.InBounds(map))
            {
                reason = "Outside current map";
                return false;
            }

            if (cell.Fogged(map))
            {
                reason = "Cell is not visible";
                return false;
            }

            AcceptanceReport report;
            if (type == RimGPTZonePlacementType.Growing)
            {
                report = new Designator_ZoneAdd_Growing().CanDesignateCell(cell);
            }
            else
            {
                report = Designator_ZoneAdd.IsZoneableCell(cell, map);
            }

            if (!report)
            {
                reason = SafeReportReason(report, "Cell cannot be added to this zone");
                return false;
            }

            return true;
        }

        public static List<IntVec3> CollectValidCells(Map map, int minX, int minZ, int maxX, int maxZ, RimGPTZonePlacementType type)
        {
            List<IntVec3> valid = new List<IntVec3>();
            NormalizeRect(ref minX, ref minZ, ref maxX, ref maxZ);
            for (int x = minX; x <= maxX; x++)
            {
                for (int z = minZ; z <= maxZ; z++)
                {
                    string reason;
                    IntVec3 cell = new IntVec3(x, 0, z);
                    if (CanCreateZoneCell(map, cell, type, out reason))
                    {
                        valid.Add(cell);
                    }
                }
            }

            return valid;
        }

        public static string SafeReportReason(AcceptanceReport report, string fallback)
        {
            try
            {
                if (!string.IsNullOrEmpty(report.Reason))
                {
                    return report.Reason;
                }
            }
            catch
            {
            }

            return fallback;
        }
    }
}
