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
        DesignateDeconstruct,
        EquipWeapon,
        DropPrimaryWeapon,
        WearApparel,
        RemoveApparel,
        AssignBed,
        UnassignBed,
        AddBill,
        SetBillSuspended,
        RemoveBill,
        SetBillTargetCount,
        SetPowerSwitch,
        SetTargetFuelLevel,
        CreateAllowedArea,
        SetAllowedAreaCells,
        AssignAllowedArea,
        PrioritizeHaul,
        PrioritizeRescue,
        PrioritizeTend,
        PrioritizeClean,
        PrioritizeRefuel,
        PrioritizeConstruct
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
        public string BedId { get; set; }
        public string WorktableId { get; set; }
        public string BillId { get; set; }
        public string RecipeDef { get; set; }
        public string RepeatMode { get; set; }
        public int TargetCount { get; set; }
        public bool HasTargetCount { get; set; }
        public bool Suspended { get; set; }
        public bool On { get; set; }
        public float Level { get; set; }
        public string Label { get; set; }
        public string AreaId { get; set; }
        public bool HasAreaId { get; set; }
        public bool Allowed { get; set; }
        public string TargetPawnId { get; set; }
        public string BlueprintOrFrameId { get; set; }
        public System.Collections.Generic.List<RimGPTCell> Cells { get; set; }

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
                    case RimGPTCommandType.EquipWeapon:
                        return "equipWeapon";
                    case RimGPTCommandType.DropPrimaryWeapon:
                        return "dropPrimaryWeapon";
                    case RimGPTCommandType.WearApparel:
                        return "wearApparel";
                    case RimGPTCommandType.RemoveApparel:
                        return "removeApparel";
                    case RimGPTCommandType.AssignBed:
                        return "assignBed";
                    case RimGPTCommandType.UnassignBed:
                        return "unassignBed";
                    case RimGPTCommandType.AddBill:
                        return "addBill";
                    case RimGPTCommandType.SetBillSuspended:
                        return "setBillSuspended";
                    case RimGPTCommandType.RemoveBill:
                        return "removeBill";
                    case RimGPTCommandType.SetBillTargetCount:
                        return "setBillTargetCount";
                    case RimGPTCommandType.SetPowerSwitch:
                        return "setPowerSwitch";
                    case RimGPTCommandType.SetTargetFuelLevel:
                        return "setTargetFuelLevel";
                    case RimGPTCommandType.CreateAllowedArea:
                        return "createAllowedArea";
                    case RimGPTCommandType.SetAllowedAreaCells:
                        return "setAllowedAreaCells";
                    case RimGPTCommandType.AssignAllowedArea:
                        return "assignAllowedArea";
                    case RimGPTCommandType.PrioritizeHaul:
                        return "prioritizeHaul";
                    case RimGPTCommandType.PrioritizeRescue:
                        return "prioritizeRescue";
                    case RimGPTCommandType.PrioritizeTend:
                        return "prioritizeTend";
                    case RimGPTCommandType.PrioritizeClean:
                        return "prioritizeClean";
                    case RimGPTCommandType.PrioritizeRefuel:
                        return "prioritizeRefuel";
                    case RimGPTCommandType.PrioritizeConstruct:
                        return "prioritizeConstruct";
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

    public sealed class RimGPTCell
    {
        public int X { get; set; }
        public int Z { get; set; }
    }
}
