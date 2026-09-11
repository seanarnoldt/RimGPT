using System.Collections.Generic;

namespace RimGPT
{
    public sealed class RimGPTStateModel
    {
        public RimGPTSnapshotState Snapshot;
        public RimGPTGameState Game;
        public RimGPTColonyState Colony;
        public List<RimGPTColonistState> Colonists;
        public RimGPTResourcesState Resources;
        public RimGPTMapThingsState MapThings;
        public RimGPTResearchState Research;
        public List<RimGPTThreatState> Threats;
        public string AwarenessJson;
        public string MapJson;
        public string BuildingsJson;
        public string OperationsJson;
    }

    public sealed class RimGPTSnapshotState
    {
        public long Version;
        public string CapturedAtUtc;
        public int TicksGame;
    }

    public sealed class RimGPTGameState
    {
        public bool Loaded;
        public bool Paused;
        public int Speed;
        public int TicksGame;
        public string Date;
        public string TimeOfDay;
        public string CurrentMapId;
        public string ColonyLineageId;
    }

    public sealed class RimGPTColonyState
    {
        public int ColonistCount;
        public int PrisonerCount;
        public int AnimalCount;
    }

    public sealed class RimGPTColonistState
    {
        public string Id;
        public string Name;
        public string KindDef;
        public string Gender;
        public int Age;
        public RimGPTPositionState Position;
        public bool Drafted;
        public RimGPTJobState CurrentJob;
        public RimGPTHealthState Health;
        public RimGPTNeedsState Needs;
        public List<RimGPTSkillState> Skills;
        public List<RimGPTWorkState> Work;
        public List<RimGPTWorkPriorityState> WorkPriorities;
        public RimGPTEquipmentState PrimaryEquipment;
        public List<RimGPTEquipmentState> Equipment;
        public List<RimGPTApparelState> Apparel;
        public RimGPTBedAssignmentState AssignedBed;
        public RimGPTAreaAssignmentState AllowedArea;
    }

    public sealed class RimGPTPositionState
    {
        public int X;
        public int Z;
    }

    public sealed class RimGPTJobState
    {
        public string DefName;
        public string Label;
    }

    public sealed class RimGPTHealthState
    {
        public string Summary;
        public bool Downed;
        public bool Dead;
        public float BleedingRate;
        public float Pain;
        public List<RimGPTHediffState> Hediffs;
    }

    public sealed class RimGPTHediffState
    {
        public string DefName;
        public string Label;
        public float Severity;
    }

    public sealed class RimGPTNeedsState
    {
        public float? Mood;
        public float? Food;
        public float? Rest;
        public float? Recreation;
    }

    public sealed class RimGPTSkillState
    {
        public string DefName;
        public int Level;
        public string Passion;
    }

    public sealed class RimGPTWorkPriorityState
    {
        public string DefName;
        public int Priority;
    }

    public sealed class RimGPTWorkState
    {
        public string DefName;
        public string Label;
        public bool Capable;
        public bool Disabled;
        public string DisabledReason;
        public int Priority;
    }

    public sealed class RimGPTEquipmentState
    {
        public string Id;
        public string DefName;
        public string Label;
        public string Quality;
        public int HitPoints;
        public int MaxHitPoints;
    }

    public sealed class RimGPTApparelState
    {
        public string Id;
        public string DefName;
        public string Label;
        public string Quality;
        public int HitPoints;
        public int MaxHitPoints;
        public bool Tainted;
    }

    public sealed class RimGPTBedAssignmentState
    {
        public string Id;
        public string DefName;
        public RimGPTPositionState Position;
    }

    public sealed class RimGPTAreaAssignmentState
    {
        public string Id;
        public string Label;
    }

    public sealed class RimGPTResourcesState
    {
        public int Silver;
        public int Wood;
        public int Steel;
        public int Plasteel;
        public int Components;
        public int AdvancedComponents;
        public int Medicine;
        public int IndustrialMedicine;
        public int GlitterworldMedicine;
        public RimGPTFoodState Food;
        public RimGPTResourceCountsState Available;
        public RimGPTResourceCountsState Forbidden;
        public RimGPTResourceCountsState TotalVisible;
    }

    public sealed class RimGPTResourceCountsState
    {
        public int Silver;
        public int Wood;
        public int Steel;
        public int Plasteel;
        public int Components;
        public int AdvancedComponents;
        public int Medicine;
        public int IndustrialMedicine;
        public int GlitterworldMedicine;
        public RimGPTFoodState Food;
    }

    public sealed class RimGPTFoodState
    {
        public float TotalNutrition;
        public int Meals;
    }

    public sealed class RimGPTResearchState
    {
        public RimGPTResearchProjectState Current;
        public List<RimGPTResearchProjectState> Available;
    }

    public sealed class RimGPTResearchProjectState
    {
        public string DefName;
        public string Label;
        public float Progress;
        public float Cost;
    }

    public sealed class RimGPTThreatState
    {
        public string Id;
        public string Type;
        public string DefName;
        public string Label;
        public string Faction;
        public string DangerReason;
        public RimGPTPositionState Position;
        public bool Downed;
        public RimGPTEquipmentState Weapon;
    }

    public sealed class RimGPTMapThingsState
    {
        public List<RimGPTMapThingState> Forbidden;
        public List<RimGPTMapThingState> Haulable;
    }

    public sealed class RimGPTMapThingState
    {
        public string Id;
        public string DefName;
        public string Label;
        public int StackCount;
        public RimGPTPositionState Position;
        public bool Forbidden;
    }
}
