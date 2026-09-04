from typing import Any


TOOLS: list[dict[str, Any]] = [
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
    raise ValueError(f"Unsupported tool call: {name}")
