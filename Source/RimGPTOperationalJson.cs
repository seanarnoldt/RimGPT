using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using RimWorld;
using Verse;

namespace RimGPT
{
    public static class RimGPTOperationalJson
    {
        private const int MaxItems = 120;
        private const int MaxBills = 40;
        private const int MaxRecipes = 160;
        private const int MaxConnectedBuildings = 80;

        public static string Build(Map map)
        {
            StringBuilder json = new StringBuilder(32768);
            json.Append("{");
            WriteEquipment(json, map, false);
            WriteApparel(json, map, true);
            WriteBeds(json, map, true);
            WriteWorktables(json, map, true);
            WritePower(json, map, true);
            WriteFuel(json, map, true);
            WriteAllowedAreas(json, map, true);
            json.Append(",\"labor\":").Append(RimGPTLaborJson.Build(map));
            json.Append("}");
            return json.ToString();
        }

        public static string BuildRecipesJson(string worktableId)
        {
            if (Current.Game == null || Find.CurrentMap == null)
            {
                return "{\"gameLoaded\":false,\"recipes\":[]}";
            }

            Thing worktable;
            IBillGiver giver;
            if (!TryGetVisibleBillGiver(worktableId, out worktable, out giver))
            {
                return "{\"error\":\"worktableNotFound\"}";
            }

            List<RecipeDef> recipes = worktable.def != null && worktable.def.AllRecipes != null
                ? worktable.def.AllRecipes
                : new List<RecipeDef>();

            StringBuilder json = new StringBuilder(16384);
            json.Append("{\"schemaVersion\":3,\"gameLoaded\":true");
            WriteString(json, "worktableId", SafeThingId(worktable), true);
            WriteString(json, "worktableDef", worktable.def != null ? worktable.def.defName : null, true);
            WriteName(json, "recipes", true);
            json.Append("[");
            int written = 0;
            for (int i = 0; i < recipes.Count && written < MaxRecipes; i++)
            {
                RecipeDef recipe = recipes[i];
                if (recipe == null)
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteRecipe(json, recipe, worktable);
                written++;
            }
            json.Append("]}");
            return json.ToString();
        }

        public static string AreaId(Area area)
        {
            return area != null ? area.GetUniqueLoadID() : null;
        }

        public static string BillId(Bill bill)
        {
            return bill != null ? bill.GetUniqueLoadID() : null;
        }

        private static void WriteEquipment(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "equipment", comma);
            json.Append("{\"availableWeapons\":[");
            int written = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count && written < MaxItems; i++)
            {
                Thing thing = things[i];
                if (!IsVisible(thing, map) || thing.def == null || !thing.def.IsWeapon)
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteWeapon(json, thing, map);
                written++;
            }
            json.Append("]}");
        }

        private static void WriteApparel(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "apparel", comma);
            json.Append("{\"available\":[");
            int written = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count && written < MaxItems; i++)
            {
                Apparel apparel = things[i] as Apparel;
                if (!IsVisible(apparel, map) || apparel.def == null)
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteApparelThing(json, apparel, map);
                written++;
            }
            json.Append("]}");
        }

        private static void WriteBeds(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "beds", comma);
            json.Append("[");
            int written = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count && written < MaxItems; i++)
            {
                Building_Bed bed = things[i] as Building_Bed;
                if (!IsVisible(bed, map) || bed.def == null)
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteBed(json, bed);
                written++;
            }
            json.Append("]");
        }

        private static void WriteWorktables(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "worktables", comma);
            json.Append("[");
            int written = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count && written < MaxItems; i++)
            {
                Thing thing = things[i];
                IBillGiver giver = thing as IBillGiver;
                if (!IsVisible(thing, map) || giver == null || thing.def == null || giver.BillStack == null)
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteWorktable(json, thing, giver);
                written++;
            }
            json.Append("]");
        }

        private static void WritePower(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "power", comma);
            json.Append("{\"networks\":[");
            int written = 0;
            if (map.powerNetManager != null && map.powerNetManager.AllNetsListForReading != null)
            {
                List<PowerNet> nets = map.powerNetManager.AllNetsListForReading;
                for (int i = 0; i < nets.Count && written < MaxItems; i++)
                {
                    PowerNet net = nets[i];
                    if (net == null)
                    {
                        continue;
                    }

                    if (written > 0)
                    {
                        json.Append(",");
                    }

                    WritePowerNet(json, net, i);
                    written++;
                }
            }
            json.Append("],\"unpoweredBuildings\":[");
            int unpowered = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count && unpowered < MaxItems; i++)
            {
                ThingWithComps thing = things[i] as ThingWithComps;
                if (!IsVisible(thing, map))
                {
                    continue;
                }

                CompPowerTrader power = thing.GetComp<CompPowerTrader>();
                if (power == null || power.PowerOn || power.PowerOutput > 0f)
                {
                    continue;
                }

                if (unpowered > 0)
                {
                    json.Append(",");
                }

                WritePowerBuilding(json, thing, power);
                unpowered++;
            }
            json.Append("]}");
        }

        private static void WriteFuel(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "fuel", comma);
            json.Append("[");
            int written = 0;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count && written < MaxItems; i++)
            {
                ThingWithComps thing = things[i] as ThingWithComps;
                if (!IsVisible(thing, map))
                {
                    continue;
                }

                CompRefuelable fuel = thing.GetComp<CompRefuelable>();
                if (fuel == null)
                {
                    continue;
                }

                if (written > 0)
                {
                    json.Append(",");
                }

                WriteFuelable(json, thing, fuel);
                written++;
            }
            json.Append("]");
        }

        private static void WriteAllowedAreas(StringBuilder json, Map map, bool comma)
        {
            WriteName(json, "allowedAreas", comma);
            json.Append("[");
            int written = 0;
            if (map.areaManager != null && map.areaManager.AllAreas != null)
            {
                List<Area> areas = map.areaManager.AllAreas;
                for (int i = 0; i < areas.Count && written < MaxItems; i++)
                {
                    Area area = areas[i];
                    if (area == null || !area.AssignableAsAllowed())
                    {
                        continue;
                    }

                    if (written > 0)
                    {
                        json.Append(",");
                    }

                    WriteArea(json, area);
                    written++;
                }
            }
            json.Append("]");
        }

        private static void WriteWeapon(StringBuilder json, Thing thing, Map map)
        {
            json.Append("{");
            WriteThingIdentity(json, thing, false);
            WriteString(json, "weaponType", WeaponType(thing.def), true);
            WriteString(json, "quality", QualityOf(thing), true);
            WriteInt(json, "hitPoints", thing.HitPoints, true);
            WriteInt(json, "maxHitPoints", thing.MaxHitPoints, true);
            WriteFloat(json, "marketValue", SafeFloat(delegate { return thing.MarketValue; }), true);
            WriteFloat(json, "range", WeaponRange(thing.def), true);
            WriteBool(json, "forbidden", thing.IsForbidden(Faction.OfPlayer), true);
            WriteBool(json, "reserved", IsReserved(thing, map), true);
            WritePosition(json, "position", thing.Position, true);
            json.Append("}");
        }

        private static void WriteApparelThing(StringBuilder json, Apparel apparel, Map map)
        {
            json.Append("{");
            WriteThingIdentity(json, apparel, false);
            WriteString(json, "quality", QualityOf(apparel), true);
            WriteInt(json, "hitPoints", apparel.HitPoints, true);
            WriteInt(json, "maxHitPoints", apparel.MaxHitPoints, true);
            WriteString(json, "wornBy", apparel.Wearer != null ? apparel.Wearer.ThingID : null, true);
            WriteBool(json, "tainted", apparel.WornByCorpse, true);
            WriteFloat(json, "marketValue", SafeFloat(delegate { return apparel.MarketValue; }), true);
            WriteFloat(json, "armorSharp", StatValue(apparel, StatDefOf.ArmorRating_Sharp), true);
            WriteFloat(json, "armorBlunt", StatValue(apparel, StatDefOf.ArmorRating_Blunt), true);
            WriteFloat(json, "insulationCold", StatValue(apparel, StatDefOf.Insulation_Cold), true);
            WriteFloat(json, "insulationHeat", StatValue(apparel, StatDefOf.Insulation_Heat), true);
            WriteBool(json, "forbidden", apparel.IsForbidden(Faction.OfPlayer), true);
            WritePosition(json, "position", apparel.Position, true);
            json.Append("}");
        }

        private static void WriteBed(StringBuilder json, Building_Bed bed)
        {
            json.Append("{");
            WriteThingIdentity(json, bed, false);
            WritePosition(json, "position", bed.Position, true);
            WriteBool(json, "medical", bed.Medical, true);
            WriteBool(json, "forPrisoners", bed.ForPrisoners, true);
            WriteName(json, "owners", true);
            json.Append("[");
            List<Pawn> owners = bed.OwnersForReading;
            if (owners != null)
            {
                for (int i = 0; i < owners.Count; i++)
                {
                    if (i > 0)
                    {
                        json.Append(",");
                    }

                    WriteStringValue(json, owners[i] != null ? owners[i].ThingID : null);
                }
            }
            json.Append("]}");
        }

        private static void WriteWorktable(StringBuilder json, Thing thing, IBillGiver giver)
        {
            json.Append("{");
            WriteThingIdentity(json, thing, false);
            WritePosition(json, "position", thing.Position, true);
            WriteNullableBool(json, "powered", PoweredState(thing), true);
            WriteNullableBool(json, "fueled", FueledState(thing), true);
            WriteBool(json, "operational", SafeBool(delegate { return giver.CurrentlyUsableForBills(); }), true);
            WriteBills(json, giver.BillStack, true);
            json.Append("}");
        }

        private static void WriteBills(StringBuilder json, BillStack stack, bool comma)
        {
            WriteName(json, "bills", comma);
            json.Append("[");
            if (stack != null && stack.Bills != null)
            {
                List<Bill> bills = stack.Bills;
                for (int i = 0; i < bills.Count && i < MaxBills; i++)
                {
                    if (i > 0)
                    {
                        json.Append(",");
                    }

                    WriteBill(json, bills[i]);
                }
            }
            json.Append("]");
        }

        private static void WriteBill(StringBuilder json, Bill bill)
        {
            Bill_Production production = bill as Bill_Production;
            json.Append("{");
            WriteString(json, "id", BillId(bill), false);
            WriteString(json, "recipeDef", bill != null && bill.recipe != null ? bill.recipe.defName : null, true);
            WriteString(json, "label", bill != null ? bill.Label : null, true);
            WriteString(json, "repeatMode", production != null && production.repeatMode != null ? production.repeatMode.defName : null, true);
            WriteInt(json, "targetCount", production != null ? production.targetCount : 0, true);
            WriteInt(json, "repeatCount", production != null ? production.repeatCount : 0, true);
            WriteBool(json, "suspended", bill != null && bill.suspended, true);
            WriteFloat(json, "ingredientSearchRadius", bill != null ? bill.ingredientSearchRadius : 0f, true);
            WriteString(json, "pawnRestriction", null, true);
            json.Append("}");
        }

        private static void WriteRecipe(StringBuilder json, RecipeDef recipe, Thing worktable)
        {
            json.Append("{");
            WriteString(json, "recipeDef", recipe.defName, false);
            WriteString(json, "label", recipe.label, true);
            WriteBool(json, "currentlyAvailable", SafeBool(delegate { return recipe.AvailableOnNow(worktable); }), true);
            WriteString(json, "workSkill", recipe.workSkill != null ? recipe.workSkill.defName : null, true);
            WriteIngredients(json, recipe, true);
            WriteProducts(json, recipe, true);
            WriteSkillRequirements(json, recipe, true);
            json.Append("}");
        }

        private static void WriteIngredients(StringBuilder json, RecipeDef recipe, bool comma)
        {
            WriteName(json, "ingredients", comma);
            json.Append("[");
            if (recipe.ingredients != null)
            {
                for (int i = 0; i < recipe.ingredients.Count; i++)
                {
                    if (i > 0)
                    {
                        json.Append(",");
                    }

                    IngredientCount ingredient = recipe.ingredients[i];
                    json.Append("{");
                    WriteString(json, "summary", ingredient != null ? ingredient.Summary : null, false);
                    WriteFloat(json, "count", ingredient != null ? ingredient.GetBaseCount() : 0f, true);
                    json.Append("}");
                }
            }
            json.Append("]");
        }

        private static void WriteProducts(StringBuilder json, RecipeDef recipe, bool comma)
        {
            WriteName(json, "products", comma);
            json.Append("[");
            if (recipe.products != null)
            {
                for (int i = 0; i < recipe.products.Count; i++)
                {
                    if (i > 0)
                    {
                        json.Append(",");
                    }

                    ThingDefCountClass product = recipe.products[i];
                    json.Append("{");
                    WriteString(json, "defName", product.thingDef != null ? product.thingDef.defName : null, false);
                    WriteString(json, "label", product.thingDef != null ? product.thingDef.label : null, true);
                    WriteInt(json, "count", product.count, true);
                    json.Append("}");
                }
            }
            json.Append("]");
        }

        private static void WriteSkillRequirements(StringBuilder json, RecipeDef recipe, bool comma)
        {
            WriteName(json, "skillRequirements", comma);
            json.Append("[");
            if (recipe.skillRequirements != null)
            {
                for (int i = 0; i < recipe.skillRequirements.Count; i++)
                {
                    if (i > 0)
                    {
                        json.Append(",");
                    }

                    SkillRequirement requirement = recipe.skillRequirements[i];
                    json.Append("{");
                    WriteString(json, "defName", requirement.skill != null ? requirement.skill.defName : null, false);
                    WriteInt(json, "minLevel", requirement.minLevel, true);
                    json.Append("}");
                }
            }
            json.Append("]");
        }

        private static void WritePowerNet(StringBuilder json, PowerNet net, int index)
        {
            float generation = 0f;
            float consumption = 0f;
            float stored = 0f;
            float capacity = 0f;

            if (net.powerComps != null)
            {
                for (int i = 0; i < net.powerComps.Count; i++)
                {
                    CompPowerTrader comp = net.powerComps[i];
                    if (comp == null)
                    {
                        continue;
                    }

                    if (comp.PowerOutput >= 0f)
                    {
                        generation += comp.PowerOutput;
                    }
                    else
                    {
                        consumption += -comp.PowerOutput;
                    }
                }
            }

            if (net.batteryComps != null)
            {
                for (int i = 0; i < net.batteryComps.Count; i++)
                {
                    CompPowerBattery battery = net.batteryComps[i];
                    if (battery == null)
                    {
                        continue;
                    }

                    stored += battery.StoredEnergy;
                    capacity += battery.Props.storedEnergyMax;
                }
            }

            json.Append("{");
            WriteString(json, "id", "powerNet-" + index.ToString(CultureInfo.InvariantCulture), false);
            WriteFloat(json, "generationWatts", generation, true);
            WriteFloat(json, "consumptionWatts", consumption, true);
            WriteFloat(json, "netWatts", generation - consumption, true);
            WriteFloat(json, "storedEnergyWd", stored, true);
            WriteFloat(json, "batteryCapacityWd", capacity, true);
            WriteName(json, "connectedBuildings", true);
            json.Append("[");
            int written = 0;
            if (net.powerComps != null)
            {
                for (int i = 0; i < net.powerComps.Count && written < MaxConnectedBuildings; i++)
                {
                    CompPowerTrader power = net.powerComps[i];
                    ThingWithComps thing = power != null ? power.parent as ThingWithComps : null;
                    if (thing == null || thing.def == null || !IsVisible(thing, thing.Map))
                    {
                        continue;
                    }

                    if (written > 0)
                    {
                        json.Append(",");
                    }

                    WritePowerBuilding(json, thing, power);
                    written++;
                }
            }
            json.Append("]}");
        }

        private static void WritePowerBuilding(StringBuilder json, ThingWithComps thing, CompPowerTrader power)
        {
            json.Append("{");
            WriteThingIdentity(json, thing, false);
            WriteString(json, "powerType", PowerType(thing), true);
            WritePosition(json, "position", thing.Position, true);
            WriteBool(json, "connected", power.PowerNet != null, true);
            WriteFloat(json, "powerOutputWatts", power.PowerOutput, true);
            WriteBool(json, "powerOn", power.PowerOn, true);
            CompPowerBattery battery = thing.GetComp<CompPowerBattery>();
            WriteFloat(json, "storedEnergyWd", battery != null ? battery.StoredEnergy : 0f, true);
            WriteFloat(json, "batteryCapacityWd", battery != null ? battery.Props.storedEnergyMax : 0f, true);
            CompFlickable flickable = thing.GetComp<CompFlickable>();
            WriteNullableBool(json, "switchedOn", flickable != null ? (bool?)flickable.SwitchIsOn : null, true);
            CompRefuelable fuel = thing.GetComp<CompRefuelable>();
            WriteNullableBool(json, "fueled", fuel != null ? (bool?)fuel.HasFuel : null, true);
            json.Append("}");
        }

        private static void WriteFuelable(StringBuilder json, ThingWithComps thing, CompRefuelable fuel)
        {
            json.Append("{");
            WriteThingIdentity(json, thing, false);
            WritePosition(json, "position", thing.Position, true);
            WriteFloat(json, "fuel", fuel.Fuel, true);
            WriteFloat(json, "fuelCapacity", fuel.Props.fuelCapacity, true);
            WriteFloat(json, "targetFuelLevel", fuel.TargetFuelLevel, true);
            WriteBool(json, "targetFuelLevelConfigurable", fuel.Props.targetFuelLevelConfigurable, true);
            WriteBool(json, "needsFuel", SafeBool(delegate { return fuel.ShouldAutoRefuelNow; }), true);
            json.Append("}");
        }

        private static void WriteArea(StringBuilder json, Area area)
        {
            json.Append("{");
            WriteString(json, "id", AreaId(area), false);
            WriteString(json, "label", area.Label, true);
            WriteInt(json, "cellCount", area.TrueCount, true);
            json.Append("}");
        }

        private static bool TryGetVisibleBillGiver(string thingId, out Thing thing, out IBillGiver giver)
        {
            thing = null;
            giver = null;
            if (string.IsNullOrEmpty(thingId) || Find.CurrentMap == null)
            {
                return false;
            }

            Map map = Find.CurrentMap;
            List<Thing> things = map.listerThings.AllThings;
            for (int i = 0; i < things.Count; i++)
            {
                Thing candidate = things[i];
                if (candidate == null || candidate.ThingID != thingId || !IsVisible(candidate, map))
                {
                    continue;
                }

                giver = candidate as IBillGiver;
                if (giver != null && giver.BillStack != null)
                {
                    thing = candidate;
                    return true;
                }
            }

            return false;
        }

        private static string WeaponType(ThingDef def)
        {
            if (def == null)
            {
                return null;
            }

            return def.IsRangedWeapon ? "ranged" : "melee";
        }

        private static float WeaponRange(ThingDef def)
        {
            if (def == null || def.Verbs == null)
            {
                return 0f;
            }

            float range = 0f;
            for (int i = 0; i < def.Verbs.Count; i++)
            {
                VerbProperties verb = def.Verbs[i];
                if (verb != null && verb.range > range)
                {
                    range = verb.range;
                }
            }

            return range;
        }

        private static string PowerType(ThingWithComps thing)
        {
            if (thing == null)
            {
                return null;
            }

            if (thing.GetComp<CompPowerBattery>() != null)
            {
                return "battery";
            }

            CompPowerTrader power = thing.GetComp<CompPowerTrader>();
            if (power != null && power.PowerOutput > 0f)
            {
                return "generator";
            }

            return "consumer";
        }

        private static bool IsReserved(Thing thing, Map map)
        {
            return SafeBool(delegate { return map.reservationManager.IsReserved(thing); });
        }

        private static bool? PoweredState(Thing thing)
        {
            ThingWithComps comps = thing as ThingWithComps;
            CompPowerTrader power = comps != null ? comps.GetComp<CompPowerTrader>() : null;
            return power != null ? (bool?)power.PowerOn : null;
        }

        private static bool? FueledState(Thing thing)
        {
            ThingWithComps comps = thing as ThingWithComps;
            CompRefuelable fuel = comps != null ? comps.GetComp<CompRefuelable>() : null;
            return fuel != null ? (bool?)fuel.HasFuel : null;
        }

        private static string SafeThingId(Thing thing)
        {
            return thing != null ? thing.ThingID : null;
        }

        private static string QualityOf(Thing thing)
        {
            CompQuality quality = thing != null ? thing.TryGetComp<CompQuality>() : null;
            return quality != null ? quality.Quality.ToString() : null;
        }

        private static string SafeLabel(Thing thing)
        {
            try
            {
                return thing != null ? thing.LabelCapNoCount : null;
            }
            catch
            {
                return thing != null && thing.def != null ? thing.def.label : null;
            }
        }

        private static float StatValue(Thing thing, StatDef stat)
        {
            return SafeFloat(delegate { return stat != null ? thing.GetStatValue(stat, true) : 0f; });
        }

        private static bool IsVisible(Thing thing, Map map)
        {
            return thing != null && map != null && thing.Spawned && thing.Map == map && !thing.Position.Fogged(map);
        }

        private static bool SafeBool(Func<bool> getter)
        {
            try
            {
                return getter();
            }
            catch
            {
                return false;
            }
        }

        private static float SafeFloat(Func<float> getter)
        {
            try
            {
                return getter();
            }
            catch
            {
                return 0f;
            }
        }

        private static void WriteThingIdentity(StringBuilder json, Thing thing, bool comma)
        {
            WriteString(json, "id", SafeThingId(thing), comma);
            WriteString(json, "defName", thing != null && thing.def != null ? thing.def.defName : null, true);
            WriteString(json, "label", SafeLabel(thing), true);
        }

        private static void WritePosition(StringBuilder json, string name, IntVec3 position, bool comma)
        {
            WriteName(json, name, comma);
            json.Append("{");
            WriteInt(json, "x", position.x, false);
            WriteInt(json, "z", position.z, true);
            json.Append("}");
        }

        private static void WriteNullableBool(StringBuilder json, string name, bool? value, bool comma)
        {
            WriteName(json, name, comma);
            if (value.HasValue)
            {
                json.Append(value.Value ? "true" : "false");
            }
            else
            {
                json.Append("null");
            }
        }

        private static void WriteString(StringBuilder json, string name, string value, bool comma)
        {
            WriteName(json, name, comma);
            WriteStringValue(json, value);
        }

        private static void WriteStringValue(StringBuilder json, string value)
        {
            if (value == null)
            {
                json.Append("null");
            }
            else
            {
                json.Append("\"").Append(RimGPTJson.Escape(value)).Append("\"");
            }
        }

        private static void WriteInt(StringBuilder json, string name, int value, bool comma)
        {
            WriteName(json, name, comma);
            json.Append(value.ToString(CultureInfo.InvariantCulture));
        }

        private static void WriteFloat(StringBuilder json, string name, float value, bool comma)
        {
            WriteName(json, name, comma);
            json.Append(value.ToString("0.###", CultureInfo.InvariantCulture));
        }

        private static void WriteBool(StringBuilder json, string name, bool value, bool comma)
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

            json.Append("\"").Append(name).Append("\":");
        }
    }
}
