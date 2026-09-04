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
        SetWorkPriority
    }

    public sealed class RimGPTCommand
    {
        public string CommandId { get; private set; }
        public RimGPTCommandType Type { get; private set; }
        public int Speed { get; set; }
        public string PawnId { get; set; }
        public int X { get; set; }
        public int Z { get; set; }
        public string WorkType { get; set; }
        public int Priority { get; set; }

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
                    default:
                        return "unknown";
                }
            }
        }
    }
}
