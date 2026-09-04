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
        DesignateHunt
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
                    default:
                        return "unknown";
                }
            }
        }
    }
}
