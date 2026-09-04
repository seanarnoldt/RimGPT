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
    raise ValueError(f"Unsupported tool call: {name}")
