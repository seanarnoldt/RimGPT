using System;
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Text;
using RimWorld;
using Verse;

namespace RimGPT
{
    public static class RimGPTPlayerAwarenessJson
    {
        private const int MaxActiveAlerts = 20;
        private const int MaxActiveLetters = 20;
        private const int MaxRecentEvents = 24;
        private const int MaxTextLength = 1200;
        private static readonly FieldInfo ActiveAlertsField = typeof(AlertsReadout).GetField("activeAlerts", BindingFlags.Instance | BindingFlags.NonPublic);
        private static readonly FieldInfo LiveMessagesField = typeof(Messages).GetField("liveMessages", BindingFlags.Static | BindingFlags.NonPublic);
        private static readonly List<AwarenessEntry> RecentEvents = new List<AwarenessEntry>();
        private static readonly HashSet<string> SeenEventIds = new HashSet<string>();
        private static bool reflectionWarningLogged;
        private static string currentScope;

        public static string Build()
        {
            ResetForNewGameIfNeeded();
            CaptureVisibleEvents();
            StringBuilder json = new StringBuilder(8192);
            json.Append("{\"activeAlerts\":[");
            WriteActiveAlerts(json);
            json.Append("],\"activeLetters\":[");
            WriteActiveLetters(json);
            json.Append("],\"recentEvents\":[");
            for (int i = 0; i < RecentEvents.Count; i++)
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                WriteEntry(json, RecentEvents[i]);
            }
            json.Append("]}");
            return json.ToString();
        }

        private static void ResetForNewGameIfNeeded()
        {
            string scope = null;
            try
            {
                RimGPTGameComponent component = Current.Game != null ? Current.Game.GetComponent<RimGPTGameComponent>() : null;
                scope = component != null ? component.ColonyLineageId : null;
            }
            catch
            {
                scope = null;
            }

            if (scope == currentScope)
            {
                return;
            }
            currentScope = scope;
            RecentEvents.Clear();
            SeenEventIds.Clear();
        }

        private static void CaptureVisibleEvents()
        {
            try
            {
                List<Message> messages = LiveMessagesField != null ? LiveMessagesField.GetValue(null) as List<Message> : null;
                if (messages != null)
                {
                    for (int i = 0; i < messages.Count; i++)
                    {
                        Message message = messages[i];
                        if (message == null)
                        {
                            continue;
                        }

                        AddRecent(new AwarenessEntry
                        {
                            Id = "message-" + message.GetUniqueLoadID(),
                            Type = message.def != null ? message.def.defName : "Message",
                            Severity = MessageSeverity(message.def),
                            Title = message.def != null ? message.def.label : "Message",
                            Text = message.text,
                            TicksGame = message.startingTick
                        });
                    }
                }

                List<Letter> letters = Find.LetterStack != null ? Find.LetterStack.LettersListForReading : null;
                if (letters != null)
                {
                    for (int i = 0; i < letters.Count; i++)
                    {
                        Letter letter = letters[i];
                        if (letter == null || !letter.CanShowInLetterStack)
                        {
                            continue;
                        }

                        AddRecent(EntryForLetter(letter));
                    }
                }
            }
            catch (Exception ex)
            {
                LogReflectionWarning(ex);
            }
        }

        private static void WriteActiveAlerts(StringBuilder json)
        {
            try
            {
                List<Alert> alerts = Find.Alerts != null && ActiveAlertsField != null
                    ? ActiveAlertsField.GetValue(Find.Alerts) as List<Alert>
                    : null;
                if (alerts == null)
                {
                    return;
                }

                int written = 0;
                for (int i = 0; i < alerts.Count && written < MaxActiveAlerts; i++)
                {
                    Alert alert = alerts[i];
                    if (alert == null || !alert.Active)
                    {
                        continue;
                    }

                    if (written > 0)
                    {
                        json.Append(",");
                    }

                    string type = alert.GetType().Name;
                    WriteEntry(json, new AwarenessEntry
                    {
                        Id = "alert-" + type,
                        Type = type,
                        Severity = alert.Priority.ToString(),
                        Title = SafeString(delegate { return alert.GetLabel(); }),
                        Text = SafeString(delegate { return alert.GetExplanation().ToString(); }),
                        TicksGame = Find.TickManager != null ? Find.TickManager.TicksGame : 0
                    });
                    written++;
                }
            }
            catch (Exception ex)
            {
                LogReflectionWarning(ex);
            }
        }

        private static void WriteActiveLetters(StringBuilder json)
        {
            List<Letter> letters = Find.LetterStack != null ? Find.LetterStack.LettersListForReading : null;
            if (letters == null)
            {
                return;
            }

            int written = 0;
            for (int i = letters.Count - 1; i >= 0 && written < MaxActiveLetters; i--)
            {
                Letter letter = letters[i];
                if (letter == null || !letter.CanShowInLetterStack)
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteEntry(json, EntryForLetter(letter));
                written++;
            }
        }

        private static AwarenessEntry EntryForLetter(Letter letter)
        {
            ChoiceLetter choice = letter as ChoiceLetter;
            return new AwarenessEntry
            {
                Id = "letter-" + letter.GetUniqueLoadID(),
                Type = letter.def != null ? letter.def.defName : letter.GetType().Name,
                Severity = LetterSeverity(letter.def),
                Title = SafeString(delegate { return letter.Label.ToString(); }),
                Text = choice != null ? SafeString(delegate { return choice.Text.ToString(); }) : null,
                TicksGame = letter.arrivalTick
            };
        }

        private static void AddRecent(AwarenessEntry entry)
        {
            if (entry == null || string.IsNullOrEmpty(entry.Id) || SeenEventIds.Contains(entry.Id))
            {
                return;
            }

            SeenEventIds.Add(entry.Id);
            RecentEvents.Insert(0, entry);
            while (RecentEvents.Count > MaxRecentEvents)
            {
                AwarenessEntry removed = RecentEvents[RecentEvents.Count - 1];
                RecentEvents.RemoveAt(RecentEvents.Count - 1);
                SeenEventIds.Remove(removed.Id);
            }
        }

        private static string MessageSeverity(MessageTypeDef def)
        {
            string name = def != null ? def.defName : string.Empty;
            if (name.IndexOf("ThreatBig", StringComparison.OrdinalIgnoreCase) >= 0 || name.IndexOf("NegativeEvent", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "High";
            }
            if (name.IndexOf("ThreatSmall", StringComparison.OrdinalIgnoreCase) >= 0 || name.IndexOf("Caution", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "Medium";
            }
            return "Info";
        }

        private static string LetterSeverity(LetterDef def)
        {
            string name = def != null ? def.defName : string.Empty;
            if (name.IndexOf("ThreatBig", StringComparison.OrdinalIgnoreCase) >= 0 || name.IndexOf("Death", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "High";
            }
            if (name.IndexOf("Threat", StringComparison.OrdinalIgnoreCase) >= 0 || name.IndexOf("Negative", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "Medium";
            }
            return "Info";
        }

        private static void WriteEntry(StringBuilder json, AwarenessEntry entry)
        {
            json.Append("{");
            WriteString(json, "id", entry.Id, false);
            WriteString(json, "type", entry.Type, true);
            WriteString(json, "severity", entry.Severity, true);
            WriteString(json, "title", entry.Title, true);
            WriteString(json, "text", entry.Text, true);
            json.Append(",\"ticksGame\":").Append(entry.TicksGame.ToString(CultureInfo.InvariantCulture));
            json.Append("}");
        }

        private static void WriteString(StringBuilder json, string name, string value, bool comma)
        {
            if (comma)
            {
                json.Append(",");
            }
            json.Append("\"").Append(name).Append("\":");
            if (value == null)
            {
                json.Append("null");
                return;
            }
            string bounded = value.Length > MaxTextLength ? value.Substring(0, MaxTextLength) : value;
            json.Append("\"").Append(RimGPTJson.Escape(bounded)).Append("\"");
        }

        private static string SafeString(Func<string> getter)
        {
            try
            {
                return getter();
            }
            catch
            {
                return null;
            }
        }

        private static void LogReflectionWarning(Exception ex)
        {
            if (!reflectionWarningLogged)
            {
                reflectionWarningLogged = true;
                Log.Warning("[RimGPT] Could not read part of the player-visible awareness UI: " + ex.Message);
            }
        }

        private sealed class AwarenessEntry
        {
            public string Id;
            public string Type;
            public string Severity;
            public string Title;
            public string Text;
            public int TicksGame;
        }
    }
}
