namespace RimGPT
{
    public enum RimGPTCommandType
    {
        Pause,
        Unpause,
        SetSpeed,
        Draft,
        Undraft,
        Move,
        SetWorkPriority,
        Allow,
        Forbid,
        AllowAll,
        SetResearch,
        PrioritizeJob,
        DesignateMine,
        DesignateCut,
        DesignateHarvest,
        DesignateHunt,
        CreateStockpile,
        SetStockpilePriority,
        SetStockpilePreset,
        CreateGrowingZone,
        SetGrowingZonePlant,
        PlaceBlueprint,
        PlaceBlueprints,
        CancelAt,
        DesignateDeconstruct
    }

    public sealed class RimGPTCommand
    {
        public string CommandId { get; private set; }
        public RimGPTCommandType Type { get; private set; }
        public int QueuedAtMillis { get; set; }
        public int Speed { get; set; }
        public string PawnId { get; set; }
        public int X { get; set; }
        public int Z { get; set; }
        public string WorkType { get; set; }
        public int Priority { get; set; }
        public string ThingId { get; set; }
        public string TargetId { get; set; }
        public string ResearchDef { get; set; }
        public int MinX { get; set; }
        public int MinZ { get; set; }
        public int MaxX { get; set; }
        public int MaxZ { get; set; }
        public int MinimumValidCells { get; set; }
        public bool HasMinimumValidCells { get; set; }
        public string ZoneId { get; set; }
        public string StoragePriority { get; set; }
        public string Preset { get; set; }
        public string PlantDef { get; set; }
        public string BuildDef { get; set; }
        public string StuffDef { get; set; }
        public string Rotation { get; set; }
        public System.Collections.Generic.List<RimGPTBlueprintPlacement> Placements { get; set; }

        public RimGPTCommand(string commandId, RimGPTCommandType type)
        {
            CommandId = commandId;
            Type = type;
        }

        public string CommandName
        {
            get
            {
                switch (Type)
                {
                    case RimGPTCommandType.Pause:
                        return "pause";
                    case RimGPTCommandType.Unpause:
                        return "unpause";
                    case RimGPTCommandType.SetSpeed:
                        return "setSpeed";
                    case RimGPTCommandType.Draft:
                        return "draft";
                    case RimGPTCommandType.Undraft:
                        return "undraft";
                    case RimGPTCommandType.Move:
                        return "move";
                    case RimGPTCommandType.SetWorkPriority:
                        return "setWorkPriority";
                    case RimGPTCommandType.Allow:
                        return "allow";
                    case RimGPTCommandType.Forbid:
                        return "forbid";
                    case RimGPTCommandType.AllowAll:
                        return "allowAll";
                    case RimGPTCommandType.SetResearch:
                        return "setResearch";
                    case RimGPTCommandType.PrioritizeJob:
                        return "prioritizeJob";
                    case RimGPTCommandType.DesignateMine:
                        return "designateMine";
                    case RimGPTCommandType.DesignateCut:
                        return "designateCut";
                    case RimGPTCommandType.DesignateHarvest:
                        return "designateHarvest";
                    case RimGPTCommandType.DesignateHunt:
                        return "designateHunt";
                    case RimGPTCommandType.CreateStockpile:
                        return "createStockpile";
                    case RimGPTCommandType.SetStockpilePriority:
                        return "setStockpilePriority";
                    case RimGPTCommandType.SetStockpilePreset:
                        return "setStockpilePreset";
                    case RimGPTCommandType.CreateGrowingZone:
                        return "createGrowingZone";
                    case RimGPTCommandType.SetGrowingZonePlant:
                        return "setGrowingZonePlant";
                    case RimGPTCommandType.PlaceBlueprint:
                        return "placeBlueprint";
                    case RimGPTCommandType.PlaceBlueprints:
                        return "placeBlueprints";
                    case RimGPTCommandType.CancelAt:
                        return "cancelAt";
                    case RimGPTCommandType.DesignateDeconstruct:
                        return "designateDeconstruct";
                    default:
                        return "unknown";
                }
            }
        }
    }

    public sealed class RimGPTBlueprintPlacement
    {
        public string BuildDef { get; set; }
        public int X { get; set; }
        public int Z { get; set; }
        public string Rotation { get; set; }
        public string StuffDef { get; set; }
    }
}
