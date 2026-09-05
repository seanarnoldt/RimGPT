from typing import Any


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "list_capabilities",
        "description": "List compact descriptions of additional RimGPT capability groups and whether each is enabled.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "enable_capability",
        "description": "Enable one explicit additional capability group for the rest of this decision cycle. Enable only groups needed for the current plan.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": [
                        "work", "construction", "zones", "production", "equipment",
                        "power", "research", "combat", "utility",
                    ],
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_colony_state",
        "description": "Read one bounded section from the latest authoritative local RimWorld state. Use the compact summary and decision delta first; request only the exact section needed for the current decision. Live state overrides strategic memory.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": [
                        "pawns", "work", "resources", "research", "buildings", "zones",
                        "equipment", "apparel", "beds", "worktables", "bills", "power",
                        "threats", "environment",
                    ],
                    "description": "The single current-state section to retrieve.",
                }
            },
            "required": ["section"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "inspect_map",
        "description": "Read-only inspection of a bounded current-map RimWorld x/z rectangle, max about 40x40 cells. Use this before important construction, zones, mining, or growing decisions. Hidden/fogged contents are not exposed.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "min_x": {"type": "integer"},
                "min_z": {"type": "integer"},
                "max_x": {"type": "integer"},
                "max_z": {"type": "integer"},
            },
            "required": ["min_x", "min_z", "max_x", "max_z"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "list_build_options",
        "description": "Read-only bounded catalog of buildable defs currently available in normal Architect/build menus. Use category or search filters when possible; do not invent buildDef or stuffDef names.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": ["string", "null"], "description": "Optional designation category defName/label filter."},
                "search": {"type": ["string", "null"], "description": "Optional defName/label search string, such as wall, door, bed, power."},
            },
            "required": ["category", "search"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_build_info",
        "description": "Read-only detailed build info for one exact buildDef from list_build_options. Use before placement when costs, size, or stuffability are uncertain.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"def_name": {"type": "string"}},
            "required": ["def_name"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "list_growable_plants",
        "description": "Read-only catalog of sowable plant defs available to growing zones, including fertility and skill constraints where RimWorld exposes them.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "check_build_placements",
        "description": "Read-only validation for up to 100 planned construction blueprints using normal RimWorld placement rules. Use this before committing large place_blueprints batches, especially on mixed terrain where Light/Medium/Heavy terrain support may vary.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "placements": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "build_def": {"type": "string"},
                            "x": {"type": "integer"},
                            "z": {"type": "integer"},
                            "rotation": {"type": "string", "enum": ["North", "East", "South", "West"]},
                            "stuff_def": {"type": ["string", "null"]},
                        },
                        "required": ["build_def", "x", "z", "rotation", "stuff_def"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["placements"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "check_zone_placement",
        "description": "Read-only validation for a planned growing or stockpile zone rectangle using normal RimWorld zone placement rules. Use before create_growing_zone/create_stockpile on mixed terrain; fertility alone is not enough.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "zone_type": {"type": "string", "enum": ["growing", "stockpile"]},
                "min_x": {"type": "integer"},
                "min_z": {"type": "integer"},
                "max_x": {"type": "integer"},
                "max_z": {"type": "integer"},
            },
            "required": ["zone_type", "min_x", "min_z", "max_x", "max_z"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "set_speed",
        "description": "Set RimWorld game speed. Use 0 for paused, 1 for normal, 2 for fast, and 3 for superfast.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "speed": {
                    "type": "integer",
                    "enum": [0, 1, 2, 3],
                    "description": "0 pauses the game; 1 normal; 2 fast; 3 superfast.",
                }
            },
            "required": ["speed"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "draft",
        "description": "Draft a player-controlled colonist by stable RimWorld ThingID. Only works for alive spawned player colonists that can be drafted.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "pawn_id": {
                    "type": "string",
                    "description": "Stable RimWorld pawn ThingID from the supplied state.",
                }
            },
            "required": ["pawn_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "undraft",
        "description": "Undraft a player-controlled colonist by stable RimWorld ThingID. This is idempotent if the pawn is already undrafted.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "pawn_id": {
                    "type": "string",
                    "description": "Stable RimWorld pawn ThingID from the supplied state.",
                }
            },
            "required": ["pawn_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "move",
        "description": "Issue a normal RimWorld move order to a drafted player-controlled colonist. The pawn must already be drafted. Coordinates are current-map RimWorld x/z cells.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "pawn_id": {
                    "type": "string",
                    "description": "Stable RimWorld pawn ThingID from the supplied state.",
                },
                "x": {
                    "type": "integer",
                    "description": "Destination RimWorld map x coordinate.",
                },
                "z": {
                    "type": "integer",
                    "description": "Destination RimWorld map z coordinate.",
                },
            },
            "required": ["pawn_id", "x", "z"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "set_work_priority",
        "description": "Set a player colonist work priority by WorkTypeDef.defName. Priority 0 disables the work; priorities 1-4 enable it, where smaller nonzero numbers are higher priority.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "pawn_id": {
                    "type": "string",
                    "description": "Stable RimWorld pawn ThingID from the supplied state.",
                },
                "work_type": {
                    "type": "string",
                    "description": "WorkTypeDef.defName, such as Doctor or Hauling, from the supplied state.",
                },
                "priority": {
                    "type": "integer",
                    "enum": [0, 1, 2, 3, 4],
                    "description": "0 disables this work. 1 is highest priority, 4 is lowest priority.",
                },
            },
            "required": ["pawn_id", "work_type", "priority"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "allow",
        "description": "Allow a visible spawned haulable thing by stable ThingID. This is useful for starting supplies that are forbidden. Hidden/fogged things cannot be targeted.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "thing_id": {
                    "type": "string",
                    "description": "Stable ThingID from mapThings.forbidden or mapThings.haulable.",
                }
            },
            "required": ["thing_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "forbid",
        "description": "Forbid a visible spawned haulable thing by stable ThingID when a normal player could forbid it. Hidden/fogged things cannot be targeted.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "thing_id": {
                    "type": "string",
                    "description": "Stable ThingID from mapThings.haulable.",
                }
            },
            "required": ["thing_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "allow_all",
        "description": "Allow all currently visible forbidden haulable things on the current map. This is especially useful at scenario start to unlock starting supplies.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "set_research",
        "description": "Set the active research project by ResearchProjectDef.defName. The project must exist, be visible/available, have prerequisites satisfied, and not already be complete.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "research_def": {
                    "type": "string",
                    "description": "ResearchProjectDef.defName, such as MicroelectronicsBasics.",
                }
            },
            "required": ["research_def"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "prioritize_job",
        "description": "Ask a colonist to prioritize an unambiguous normal-player job on a visible target. Currently this is conservative and may fail when ambiguous; use it primarily for visible allowed haulable targets.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "pawn_id": {
                    "type": "string",
                    "description": "Stable RimWorld pawn ThingID from the supplied state.",
                },
                "target_id": {
                    "type": "string",
                    "description": "Visible target ThingID from the supplied state.",
                },
            },
            "required": ["pawn_id", "target_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "designate_mine",
        "description": "Designate a visible mineable rock/ore at current-map RimWorld x/z coordinates. Hidden/fogged cells cannot be targeted.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Current-map RimWorld x coordinate."},
                "z": {"type": "integer", "description": "Current-map RimWorld z coordinate."},
            },
            "required": ["x", "z"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "designate_cut",
        "description": "Designate a visible plant/tree for cutting at current-map RimWorld x/z coordinates. Hidden/fogged cells cannot be targeted.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Current-map RimWorld x coordinate."},
                "z": {"type": "integer", "description": "Current-map RimWorld z coordinate."},
            },
            "required": ["x", "z"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "designate_harvest",
        "description": "Designate a visible mature harvestable plant at current-map RimWorld x/z coordinates. Hidden/fogged cells cannot be targeted.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Current-map RimWorld x coordinate."},
                "z": {"type": "integer", "description": "Current-map RimWorld z coordinate."},
            },
            "required": ["x", "z"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "designate_hunt",
        "description": "Designate a visible spawned wild animal for hunting by stable ThingID. This cannot target player animals, colonists, hidden pawns, or non-animals.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "thing_id": {
                    "type": "string",
                    "description": "Stable visible animal ThingID from the supplied state.",
                }
            },
            "required": ["thing_id"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_stockpile",
        "description": "Create a normal stockpile zone over visible valid cells in a current-map x/z rectangle. Does not overlap existing zones. Returns a zoneId for later stockpile configuration.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "min_x": {"type": "integer"},
                "min_z": {"type": "integer"},
                "max_x": {"type": "integer"},
                "max_z": {"type": "integer"},
            },
            "required": ["min_x", "min_z", "max_x", "max_z"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "set_stockpile_priority",
        "description": "Set normal RimWorld storage priority for a stockpile zone ID returned by state/create_stockpile. Valid priorities: Low, Normal, Preferred, Important, Critical.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "zone_id": {"type": "string"},
                "priority": {"type": "string", "enum": ["Low", "Normal", "Preferred", "Important", "Critical"]},
            },
            "required": ["zone_id", "priority"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "set_stockpile_preset",
        "description": "Set a bounded stockpile storage filter preset. Valid presets: all, food, rawResources, manufactured, weapons, apparel, chunks, corpses, nothing.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "zone_id": {"type": "string"},
                "preset": {"type": "string", "enum": ["all", "food", "rawResources", "manufactured", "weapons", "apparel", "chunks", "corpses", "nothing"]},
            },
            "required": ["zone_id", "preset"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_growing_zone",
        "description": "Create a normal growing zone on visible valid cells in a current-map x/z rectangle. Use inspect_map/check_zone_placement first. minimum_valid_cells prevents accidental tiny farms; omit or use null to preserve permissive behavior.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "min_x": {"type": "integer"},
                "min_z": {"type": "integer"},
                "max_x": {"type": "integer"},
                "max_z": {"type": "integer"},
                "minimum_valid_cells": {
                    "type": ["integer", "null"],
                    "description": "Optional minimum number of valid cells required before creating the zone. Use this to avoid accidental 1-2 cell farms.",
                },
            },
            "required": ["min_x", "min_z", "max_x", "max_z", "minimum_valid_cells"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "set_growing_zone_plant",
        "description": "Set a growing zone plant by exact plantDef from list_growable_plants. The plant must be normally sowable in at least part of the zone.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "zone_id": {"type": "string"},
                "plant_def": {"type": "string"},
            },
            "required": ["zone_id", "plant_def"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "place_blueprint",
        "description": "Place one normal RimWorld construction blueprint at current-map x/z coordinates. Use exact buildDef from list_build_options. Buildings are not spawned instantly; colonists must construct them. Use stuffDef only when the buildDef is stuffable.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "build_def": {"type": "string"},
                "x": {"type": "integer"},
                "z": {"type": "integer"},
                "rotation": {"type": "string", "enum": ["North", "East", "South", "West"]},
                "stuff_def": {"type": ["string", "null"]},
            },
            "required": ["build_def", "x", "z", "rotation", "stuff_def"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "place_blueprints",
        "description": "Place up to 100 normal RimWorld construction blueprints in one command. Good for walls/rooms. Per-placement failures are reported; the batch is not atomic.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "placements": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "build_def": {"type": "string"},
                            "x": {"type": "integer"},
                            "z": {"type": "integer"},
                            "rotation": {"type": "string", "enum": ["North", "East", "South", "West"]},
                            "stuff_def": {"type": ["string", "null"]},
                        },
                        "required": ["build_def", "x", "z", "rotation", "stuff_def"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["placements"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "cancel_at",
        "description": "Cancel normal player-cancellable blueprints, frames, and designations at a visible current-map x/z cell.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "z": {"type": "integer"},
            },
            "required": ["x", "z"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "designate_deconstruct",
        "description": "Designate a visible player-owned structure for normal deconstruction by stable ThingID. Does not destroy instantly.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"thing_id": {"type": "string"}},
            "required": ["thing_id"],
            "additionalProperties": False,
        },
    },
]


TOOLS.extend(
    [
        {
            "type": "function",
            "name": "list_recipes",
            "description": "Read-only list of recipes currently available for a specific visible bill-capable worktable ID from operations.worktables.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {"worktable_id": {"type": "string"}},
                "required": ["worktable_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "equip_weapon",
            "description": "Order a player colonist to equip a visible available weapon by ThingID using normal RimWorld equip job behavior.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "thing_id": {"type": "string"}}, "required": ["pawn_id", "thing_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "drop_primary_weapon",
            "description": "Order a player colonist to drop their current primary weapon using normal RimWorld job behavior. Idempotent if none is equipped.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}}, "required": ["pawn_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "wear_apparel",
            "description": "Order a player colonist to wear visible available apparel by ThingID using normal RimWorld wear job behavior.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "thing_id": {"type": "string"}}, "required": ["pawn_id", "thing_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "remove_apparel",
            "description": "Order a player colonist to remove a currently worn apparel ThingID using normal RimWorld remove-apparel job behavior.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "thing_id": {"type": "string"}}, "required": ["pawn_id", "thing_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "assign_bed",
            "description": "Assign an ordinary colonist bed to a player colonist. Prisoner and medical beds are intentionally unsupported.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "bed_id": {"type": "string"}}, "required": ["pawn_id", "bed_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "unassign_bed",
            "description": "Remove a player colonist's current bed assignment. Idempotent if no bed is assigned.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}}, "required": ["pawn_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "add_bill",
            "description": "Add a production bill to a visible bill-capable worktable. repeat_mode is forever, doXTimes, or untilX; doXTimes/untilX require target_count.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "worktable_id": {"type": "string"},
                    "recipe_def": {"type": "string"},
                    "repeat_mode": {"type": "string", "enum": ["forever", "doXTimes", "untilX"]},
                    "target_count": {"type": ["integer", "null"]},
                },
                "required": ["worktable_id", "recipe_def", "repeat_mode", "target_count"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "set_bill_suspended",
            "description": "Suspend or unsuspend an existing worktable bill by bill ID.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"worktable_id": {"type": "string"}, "bill_id": {"type": "string"}, "suspended": {"type": "boolean"}}, "required": ["worktable_id", "bill_id", "suspended"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "remove_bill",
            "description": "Remove an existing worktable bill by bill ID.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"worktable_id": {"type": "string"}, "bill_id": {"type": "string"}}, "required": ["worktable_id", "bill_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "set_bill_target_count",
            "description": "Set the target count for a production bill that supports target counts.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"worktable_id": {"type": "string"}, "bill_id": {"type": "string"}, "target_count": {"type": "integer"}}, "required": ["worktable_id", "bill_id", "target_count"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "set_power_switch",
            "description": "Toggle a visible structure that has an ordinary player-operable power switch. Does not fake power connections.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"thing_id": {"type": "string"}, "on": {"type": "boolean"}}, "required": ["thing_id", "on"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "set_target_fuel_level",
            "description": "Set target fuel level for a visible refuelable building when RimWorld exposes that setting.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"thing_id": {"type": "string"}, "level": {"type": "number"}}, "required": ["thing_id", "level"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "create_allowed_area",
            "description": "Create a player allowed area and return its area ID.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"label": {"type": ["string", "null"]}}, "required": ["label"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "set_allowed_area_cells",
            "description": "Set up to 400 visible current-map cells allowed or disallowed in an existing allowed area.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "area_id": {"type": "string"},
                    "cells": {"type": "array", "maxItems": 400, "items": {"type": "object", "properties": {"x": {"type": "integer"}, "z": {"type": "integer"}}, "required": ["x", "z"], "additionalProperties": False}},
                    "allowed": {"type": "boolean"},
                },
                "required": ["area_id", "cells", "allowed"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "assign_allowed_area",
            "description": "Assign a player colonist to an allowed area by area ID, or pass null to make the pawn unrestricted.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "area_id": {"type": ["string", "null"]}}, "required": ["pawn_id", "area_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "prioritize_haul",
            "description": "Order a player colonist to prioritize hauling a visible allowed haulable ThingID using normal RimWorld hauling behavior.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "thing_id": {"type": "string"}}, "required": ["pawn_id", "thing_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "prioritize_rescue",
            "description": "Order a player colonist to rescue a visible downed player pawn using normal rescue job behavior.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "target_pawn_id": {"type": "string"}}, "required": ["pawn_id", "target_pawn_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "prioritize_tend",
            "description": "Order a player colonist to tend a visible injured player pawn when a normal tend job is currently available.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "target_pawn_id": {"type": "string"}}, "required": ["pawn_id", "target_pawn_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "prioritize_clean",
            "description": "Order a player colonist to clean visible filth at current-map RimWorld x/z coordinates.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "x": {"type": "integer"}, "z": {"type": "integer"}}, "required": ["pawn_id", "x", "z"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "prioritize_refuel",
            "description": "Order a player colonist to refuel a visible refuelable building using normal RimWorld refuel job behavior.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "thing_id": {"type": "string"}}, "required": ["pawn_id", "thing_id"], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "prioritize_construct",
            "description": "Order a player colonist to prioritize a visible construction blueprint or frame by ThingID using normal construction job behavior.",
            "strict": True,
            "parameters": {"type": "object", "properties": {"pawn_id": {"type": "string"}, "blueprint_or_frame_id": {"type": "string"}}, "required": ["pawn_id", "blueprint_or_frame_id"], "additionalProperties": False},
        },
    ]
)


def is_read_only_tool(name: str) -> bool:
    from tool_registry import DEFAULT_TOOL_REGISTRY

    registration = DEFAULT_TOOL_REGISTRY.registration(name)
    return registration is not None and registration.read_only


def convert_blueprint_placements(placements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "buildDef": item["build_def"],
            "x": item["x"],
            "z": item["z"],
            "rotation": item["rotation"],
            "stuffDef": item.get("stuff_def"),
        }
        for item in placements
    ]


def tool_call_to_bridge_command(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "set_speed":
        return {"command": "setSpeed", "speed": arguments["speed"]}
    if name == "draft":
        return {"command": "draft", "pawnId": arguments["pawn_id"]}
    if name == "undraft":
        return {"command": "undraft", "pawnId": arguments["pawn_id"]}
    if name == "move":
        return {
            "command": "move",
            "pawnId": arguments["pawn_id"],
            "x": arguments["x"],
            "z": arguments["z"],
        }
    if name == "set_work_priority":
        return {
            "command": "setWorkPriority",
            "pawnId": arguments["pawn_id"],
            "workType": arguments["work_type"],
            "priority": arguments["priority"],
        }
    if name == "allow":
        return {"command": "allow", "thingId": arguments["thing_id"]}
    if name == "forbid":
        return {"command": "forbid", "thingId": arguments["thing_id"]}
    if name == "allow_all":
        return {"command": "allowAll"}
    if name == "set_research":
        return {"command": "setResearch", "researchDef": arguments["research_def"]}
    if name == "prioritize_job":
        return {
            "command": "prioritizeJob",
            "pawnId": arguments["pawn_id"],
            "targetId": arguments["target_id"],
        }
    if name == "designate_mine":
        return {"command": "designateMine", "x": arguments["x"], "z": arguments["z"]}
    if name == "designate_cut":
        return {"command": "designateCut", "x": arguments["x"], "z": arguments["z"]}
    if name == "designate_harvest":
        return {"command": "designateHarvest", "x": arguments["x"], "z": arguments["z"]}
    if name == "designate_hunt":
        return {"command": "designateHunt", "thingId": arguments["thing_id"]}
    if name == "create_stockpile":
        return {
            "command": "createStockpile",
            "minX": arguments["min_x"],
            "minZ": arguments["min_z"],
            "maxX": arguments["max_x"],
            "maxZ": arguments["max_z"],
        }
    if name == "set_stockpile_priority":
        return {"command": "setStockpilePriority", "zoneId": arguments["zone_id"], "priority": arguments["priority"]}
    if name == "set_stockpile_preset":
        return {"command": "setStockpilePreset", "zoneId": arguments["zone_id"], "preset": arguments["preset"]}
    if name == "create_growing_zone":
        command = {
            "command": "createGrowingZone",
            "minX": arguments["min_x"],
            "minZ": arguments["min_z"],
            "maxX": arguments["max_x"],
            "maxZ": arguments["max_z"],
        }
        if arguments.get("minimum_valid_cells") is not None:
            command["minimumValidCells"] = arguments["minimum_valid_cells"]
        return command
    if name == "set_growing_zone_plant":
        return {"command": "setGrowingZonePlant", "zoneId": arguments["zone_id"], "plantDef": arguments["plant_def"]}
    if name == "place_blueprint":
        return {
            "command": "placeBlueprint",
            "buildDef": arguments["build_def"],
            "x": arguments["x"],
            "z": arguments["z"],
            "rotation": arguments["rotation"],
            "stuffDef": arguments.get("stuff_def"),
        }
    if name == "place_blueprints":
        return {
            "command": "placeBlueprints",
            "placements": convert_blueprint_placements(arguments["placements"]),
        }
    if name == "cancel_at":
        return {"command": "cancelAt", "x": arguments["x"], "z": arguments["z"]}
    if name == "designate_deconstruct":
        return {"command": "designateDeconstruct", "thingId": arguments["thing_id"]}
    if name == "equip_weapon":
        return {"command": "equipWeapon", "pawnId": arguments["pawn_id"], "thingId": arguments["thing_id"]}
    if name == "drop_primary_weapon":
        return {"command": "dropPrimaryWeapon", "pawnId": arguments["pawn_id"]}
    if name == "wear_apparel":
        return {"command": "wearApparel", "pawnId": arguments["pawn_id"], "thingId": arguments["thing_id"]}
    if name == "remove_apparel":
        return {"command": "removeApparel", "pawnId": arguments["pawn_id"], "thingId": arguments["thing_id"]}
    if name == "assign_bed":
        return {"command": "assignBed", "pawnId": arguments["pawn_id"], "bedId": arguments["bed_id"]}
    if name == "unassign_bed":
        return {"command": "unassignBed", "pawnId": arguments["pawn_id"]}
    if name == "add_bill":
        command = {
            "command": "addBill",
            "worktableId": arguments["worktable_id"],
            "recipeDef": arguments["recipe_def"],
            "repeatMode": arguments["repeat_mode"],
        }
        if arguments.get("target_count") is not None:
            command["targetCount"] = arguments["target_count"]
        return command
    if name == "set_bill_suspended":
        return {
            "command": "setBillSuspended",
            "worktableId": arguments["worktable_id"],
            "billId": arguments["bill_id"],
            "suspended": arguments["suspended"],
        }
    if name == "remove_bill":
        return {"command": "removeBill", "worktableId": arguments["worktable_id"], "billId": arguments["bill_id"]}
    if name == "set_bill_target_count":
        return {
            "command": "setBillTargetCount",
            "worktableId": arguments["worktable_id"],
            "billId": arguments["bill_id"],
            "targetCount": arguments["target_count"],
        }
    if name == "set_power_switch":
        return {"command": "setPowerSwitch", "thingId": arguments["thing_id"], "on": arguments["on"]}
    if name == "set_target_fuel_level":
        return {"command": "setTargetFuelLevel", "thingId": arguments["thing_id"], "level": arguments["level"]}
    if name == "create_allowed_area":
        return {"command": "createAllowedArea", "label": arguments.get("label")}
    if name == "set_allowed_area_cells":
        return {
            "command": "setAllowedAreaCells",
            "areaId": arguments["area_id"],
            "cells": arguments["cells"],
            "allowed": arguments["allowed"],
        }
    if name == "assign_allowed_area":
        return {"command": "assignAllowedArea", "pawnId": arguments["pawn_id"], "areaId": arguments.get("area_id")}
    if name == "prioritize_haul":
        return {"command": "prioritizeHaul", "pawnId": arguments["pawn_id"], "thingId": arguments["thing_id"]}
    if name == "prioritize_rescue":
        return {
            "command": "prioritizeRescue",
            "pawnId": arguments["pawn_id"],
            "targetPawnId": arguments["target_pawn_id"],
        }
    if name == "prioritize_tend":
        return {
            "command": "prioritizeTend",
            "pawnId": arguments["pawn_id"],
            "targetPawnId": arguments["target_pawn_id"],
        }
    if name == "prioritize_clean":
        return {"command": "prioritizeClean", "pawnId": arguments["pawn_id"], "x": arguments["x"], "z": arguments["z"]}
    if name == "prioritize_refuel":
        return {"command": "prioritizeRefuel", "pawnId": arguments["pawn_id"], "thingId": arguments["thing_id"]}
    if name == "prioritize_construct":
        return {
            "command": "prioritizeConstruct",
            "pawnId": arguments["pawn_id"],
            "blueprintOrFrameId": arguments["blueprint_or_frame_id"],
        }
    raise ValueError(f"Unsupported tool call: {name}")
