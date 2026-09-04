using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace RimGPT
{
    public static class RimGPTStateJsonWriter
    {
        public static string Write(RimGPTStateModel state)
        {
            StringBuilder json = new StringBuilder(8192);
            json.Append("{\"schemaVersion\":1");
            WriteGame(json, state.Game);
            WriteColony(json, state.Colony);
            WriteColonists(json, state.Colonists);
            WriteResources(json, state.Resources);
            WriteResearch(json, state.Research);
            WriteThreats(json, state.Threats);
            json.Append("}");
            return json.ToString();
        }

        private static void WriteGame(StringBuilder json, RimGPTGameState game)
        {
            json.Append(",\"game\":{");
            WriteBoolField(json, "loaded", game.Loaded, false);
            WriteBoolField(json, "paused", game.Paused, true);
            WriteIntField(json, "speed", game.Speed, true);
            WriteIntField(json, "ticksGame", game.TicksGame, true);
            WriteStringField(json, "date", game.Date, true);
            WriteStringField(json, "timeOfDay", game.TimeOfDay, true);
            WriteStringField(json, "currentMapId", game.CurrentMapId, true);
            json.Append("}");
        }

        private static void WriteColony(StringBuilder json, RimGPTColonyState colony)
        {
            json.Append(",\"colony\":{");
            WriteIntField(json, "colonistCount", colony.ColonistCount, false);
            WriteIntField(json, "prisonerCount", colony.PrisonerCount, true);
            WriteIntField(json, "animalCount", colony.AnimalCount, true);
            json.Append("}");
        }

        private static void WriteColonists(StringBuilder json, List<RimGPTColonistState> colonists)
        {
            json.Append(",\"colonists\":[");
            for (int i = 0; i < colonists.Count; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                RimGPTColonistState colonist = colonists[i];
                json.Append("{");
                WriteStringField(json, "id", colonist.Id, false);
                WriteStringField(json, "name", colonist.Name, true);
                WriteStringField(json, "kindDef", colonist.KindDef, true);
                WriteStringField(json, "gender", colonist.Gender, true);
                WriteIntField(json, "age", colonist.Age, true);
                WritePositionField(json, "position", colonist.Position, true);
                WriteBoolField(json, "drafted", colonist.Drafted, true);
                WriteJobField(json, "currentJob", colonist.CurrentJob, true);
                WriteHealthField(json, "health", colonist.Health, true);
                WriteNeedsField(json, "needs", colonist.Needs, true);
                WriteSkillsField(json, "skills", colonist.Skills, true);
                WriteWorkPrioritiesField(json, "workPriorities", colonist.WorkPriorities, true);
                WriteEquipmentArrayField(json, "equipment", colonist.Equipment, true);
                json.Append("}");
            }
            json.Append("]");
        }

        private static void WriteResources(StringBuilder json, RimGPTResourcesState resources)
        {
            json.Append(",\"resources\":{");
            WriteIntField(json, "silver", resources.Silver, false);
            WriteIntField(json, "wood", resources.Wood, true);
            WriteIntField(json, "steel", resources.Steel, true);
            WriteIntField(json, "plasteel", resources.Plasteel, true);
            WriteIntField(json, "components", resources.Components, true);
            WriteIntField(json, "advancedComponents", resources.AdvancedComponents, true);
            WriteIntField(json, "medicine", resources.Medicine, true);
            WriteIntField(json, "industrialMedicine", resources.IndustrialMedicine, true);
            WriteIntField(json, "glitterworldMedicine", resources.GlitterworldMedicine, true);
            json.Append(",\"food\":{");
            WriteFloatField(json, "totalNutrition", resources.Food.TotalNutrition, false);
            WriteIntField(json, "meals", resources.Food.Meals, true);
            json.Append("}}");
        }

        private static void WriteResearch(StringBuilder json, RimGPTResearchState research)
        {
            json.Append(",\"research\":{");
            json.Append("\"current\":");
            if (research.Current == null)
            {
                json.Append("null");
            }
            else
            {
                json.Append("{");
                WriteStringField(json, "defName", research.Current.DefName, false);
                WriteStringField(json, "label", research.Current.Label, true);
                WriteFloatField(json, "progress", research.Current.Progress, true);
                WriteFloatField(json, "cost", research.Current.Cost, true);
                json.Append("}");
            }
            json.Append("}");
        }

        private static void WriteThreats(StringBuilder json, List<RimGPTThreatState> threats)
        {
            json.Append(",\"threats\":[");
            for (int i = 0; i < threats.Count; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                RimGPTThreatState threat = threats[i];
                json.Append("{");
                WriteStringField(json, "id", threat.Id, false);
                WriteStringField(json, "type", threat.Type, true);
                WriteStringField(json, "defName", threat.DefName, true);
                WriteStringField(json, "label", threat.Label, true);
                WriteStringField(json, "faction", threat.Faction, true);
                WritePositionField(json, "position", threat.Position, true);
                WriteBoolField(json, "downed", threat.Downed, true);
                WriteEquipmentField(json, "weapon", threat.Weapon, true);
                json.Append("}");
            }
            json.Append("]");
        }

        private static void WritePositionField(StringBuilder json, string name, RimGPTPositionState position, bool comma)
        {
            WriteName(json, name, comma);
            if (position == null)
            {
                json.Append("null");
                return;
            }

            json.Append("{");
            WriteIntField(json, "x", position.X, false);
            WriteIntField(json, "z", position.Z, true);
            json.Append("}");
        }

        private static void WriteJobField(StringBuilder json, string name, RimGPTJobState job, bool comma)
        {
            WriteName(json, name, comma);
            if (job == null)
            {
                json.Append("null");
                return;
            }

            json.Append("{");
            WriteStringField(json, "defName", job.DefName, false);
            WriteStringField(json, "label", job.Label, true);
            json.Append("}");
        }

        private static void WriteHealthField(StringBuilder json, string name, RimGPTHealthState health, bool comma)
        {
            WriteName(json, name, comma);
            json.Append("{");
            WriteStringField(json, "summary", health.Summary, false);
            WriteBoolField(json, "downed", health.Downed, true);
            WriteBoolField(json, "dead", health.Dead, true);
            WriteFloatField(json, "bleedingRate", health.BleedingRate, true);
            WriteFloatField(json, "pain", health.Pain, true);
            json.Append(",\"hediffs\":[");
            for (int i = 0; i < health.Hediffs.Count; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                RimGPTHediffState hediff = health.Hediffs[i];
                json.Append("{");
                WriteStringField(json, "defName", hediff.DefName, false);
                WriteStringField(json, "label", hediff.Label, true);
                WriteFloatField(json, "severity", hediff.Severity, true);
                json.Append("}");
            }
            json.Append("]}");
        }

        private static void WriteNeedsField(StringBuilder json, string name, RimGPTNeedsState needs, bool comma)
        {
            WriteName(json, name, comma);
            json.Append("{");
            WriteNullableFloatField(json, "mood", needs.Mood, false);
            WriteNullableFloatField(json, "food", needs.Food, true);
            WriteNullableFloatField(json, "rest", needs.Rest, true);
            WriteNullableFloatField(json, "recreation", needs.Recreation, true);
            json.Append("}");
        }

        private static void WriteSkillsField(StringBuilder json, string name, List<RimGPTSkillState> skills, bool comma)
        {
            WriteName(json, name, comma);
            json.Append("[");
            for (int i = 0; i < skills.Count; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                RimGPTSkillState skill = skills[i];
                json.Append("{");
                WriteStringField(json, "defName", skill.DefName, false);
                WriteIntField(json, "level", skill.Level, true);
                WriteStringField(json, "passion", skill.Passion, true);
                json.Append("}");
            }
            json.Append("]");
        }

        private static void WriteWorkPrioritiesField(StringBuilder json, string name, List<RimGPTWorkPriorityState> priorities, bool comma)
        {
            WriteName(json, name, comma);
            json.Append("[");
            for (int i = 0; i < priorities.Count; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                RimGPTWorkPriorityState priority = priorities[i];
                json.Append("{");
                WriteStringField(json, "defName", priority.DefName, false);
                WriteIntField(json, "priority", priority.Priority, true);
                json.Append("}");
            }
            json.Append("]");
        }

        private static void WriteEquipmentArrayField(StringBuilder json, string name, List<RimGPTEquipmentState> equipment, bool comma)
        {
            WriteName(json, name, comma);
            json.Append("[");
            for (int i = 0; i < equipment.Count; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                WriteEquipmentObject(json, equipment[i]);
            }
            json.Append("]");
        }

        private static void WriteEquipmentField(StringBuilder json, string name, RimGPTEquipmentState equipment, bool comma)
        {
            WriteName(json, name, comma);
            if (equipment == null)
            {
                json.Append("null");
                return;
            }

            WriteEquipmentObject(json, equipment);
        }

        private static void WriteEquipmentObject(StringBuilder json, RimGPTEquipmentState equipment)
        {
            json.Append("{");
            WriteStringField(json, "id", equipment.Id, false);
            WriteStringField(json, "defName", equipment.DefName, true);
            WriteStringField(json, "label", equipment.Label, true);
            json.Append("}");
        }

        private static void WriteStringField(StringBuilder json, string name, string value, bool comma)
        {
            WriteName(json, name, comma);
            if (value == null)
            {
                json.Append("null");
                return;
            }

            json.Append("\"");
            json.Append(RimGPTJson.Escape(value));
            json.Append("\"");
        }

        private static void WriteIntField(StringBuilder json, string name, int value, bool comma)
        {
            WriteName(json, name, comma);
            json.Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static void WriteFloatField(StringBuilder json, string name, float value, bool comma)
        {
            WriteName(json, name, comma);
            json.Append(value.ToString("0.###", CultureInfo.InvariantCulture));
        }

        private static void WriteNullableFloatField(StringBuilder json, string name, float? value, bool comma)
        {
            WriteName(json, name, comma);
            if (value.HasValue)
            {
                json.Append(value.Value.ToString("0.###", CultureInfo.InvariantCulture));
            }
            else
            {
                json.Append("null");
            }
        }

        private static void WriteBoolField(StringBuilder json, string name, bool value, bool comma)
        {
            WriteName(json, name, comma);
            json.Append(value ? "true" : "false");
        }

        private static void WriteName(StringBuilder json, string name, bool comma)
        {
            if (comma)
            {
                json.Append(",");
            }

            json.Append("\"");
            json.Append(name);
            json.Append("\":");
        }
    }
}
