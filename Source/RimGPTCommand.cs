namespace RimGPT
{
    public enum RimGPTCommandType
    {
        Pause,
        Unpause
    }

    public sealed class RimGPTCommand
    {
        public RimGPTCommandType Type { get; private set; }

        public RimGPTCommand(RimGPTCommandType type)
        {
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
                    default:
                        return "unknown";
                }
            }
        }
    }
}
