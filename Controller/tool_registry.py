"""Authoritative model-tool grouping and per-cycle capability activation."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from context_telemetry import serialized_chars
from tools import TOOLS


CORE_GROUP = "core"
DEFAULT_MAX_ACTIVE_TOOL_GROUPS = 3


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    description: str
    tools: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ToolRegistration:
    name: str
    group: str
    schema: dict[str, Any]
    executor: str

    @property
    def read_only(self) -> bool:
        return self.executor.startswith(("read:", "controller:"))


CAPABILITY_SPECS = (
    CapabilitySpec(
        CORE_GROUP,
        "Read selective state and map context, manage game speed, and discover additional capabilities.",
        (
            ("finish_decision", "controller:finish_decision"),
            ("get_colony_state", "read:get_colony_state"),
            ("inspect_room_at", "read:inspect_room_at"),
            ("diagnose_construction", "read:diagnose_construction"),
            ("inspect_map", "read:inspect_map"),
            ("set_speed", "command:set_speed"),
            ("list_capabilities", "controller:list_capabilities"),
            ("enable_capability", "controller:enable_capability"),
        ),
    ),
    CapabilitySpec(
        "work",
        "Set work priorities and issue supported routine work orders.",
        (
            ("set_work_priority", "command:set_work_priority"),
            ("prioritize_job", "command:prioritize_job"),
            ("prioritize_haul", "command:prioritize_haul"),
            ("prioritize_rescue", "command:prioritize_rescue"),
            ("prioritize_tend", "command:prioritize_tend"),
            ("prioritize_clean", "command:prioritize_clean"),
            ("prioritize_refuel", "command:prioritize_refuel"),
            ("prioritize_construct", "command:prioritize_construct"),
        ),
    ),
    CapabilitySpec(
        "construction",
        "Inspect building options, validate placements, place blueprints, cancel, deconstruct, and mine.",
        (
            ("list_build_options", "read:list_build_options"),
            ("get_build_info", "read:get_build_info"),
            ("check_build_placements", "read:check_build_placements"),
            ("place_blueprint", "command:place_blueprint"),
            ("place_blueprints", "command:place_blueprints"),
            ("cancel_at", "command:cancel_at"),
            ("designate_deconstruct", "command:designate_deconstruct"),
            ("designate_mine", "command:designate_mine"),
        ),
    ),
    CapabilitySpec(
        "zones",
        "Manage growing zones, stockpiles, allowed areas, and exact zone validation.",
        (
            ("list_growable_plants", "read:list_growable_plants"),
            ("check_zone_placement", "read:check_zone_placement"),
            ("create_stockpile", "command:create_stockpile"),
            ("set_stockpile_priority", "command:set_stockpile_priority"),
            ("set_stockpile_preset", "command:set_stockpile_preset"),
            ("create_growing_zone", "command:create_growing_zone"),
            ("set_growing_zone_plant", "command:set_growing_zone_plant"),
            ("create_allowed_area", "command:create_allowed_area"),
            ("set_allowed_area_cells", "command:set_allowed_area_cells"),
            ("assign_allowed_area", "command:assign_allowed_area"),
        ),
    ),
    CapabilitySpec(
        "production",
        "Inspect recipe choices and manage worktable bills.",
        (
            ("list_recipes", "read:list_recipes"),
            ("add_bill", "command:add_bill"),
            ("set_bill_suspended", "command:set_bill_suspended"),
            ("remove_bill", "command:remove_bill"),
            ("set_bill_target_count", "command:set_bill_target_count"),
        ),
    ),
    CapabilitySpec(
        "equipment",
        "Manage pawn weapons, apparel, and bed assignments.",
        (
            ("equip_weapon", "command:equip_weapon"),
            ("drop_primary_weapon", "command:drop_primary_weapon"),
            ("wear_apparel", "command:wear_apparel"),
            ("remove_apparel", "command:remove_apparel"),
            ("assign_bed", "command:assign_bed"),
            ("unassign_bed", "command:unassign_bed"),
        ),
    ),
    CapabilitySpec(
        "power",
        "Control supported power switches and target fuel levels.",
        (
            ("set_power_switch", "command:set_power_switch"),
            ("set_target_fuel_level", "command:set_target_fuel_level"),
        ),
    ),
    CapabilitySpec(
        "research",
        "Select the colony's active research project.",
        (("set_research", "command:set_research"),),
    ),
    CapabilitySpec(
        "combat",
        "High-cost tactical controls for combat or genuine immediate danger, plus visible-animal hunting. Do not enable solely for mild non-combat exposure.",
        (
            ("draft", "command:draft"),
            ("undraft", "command:undraft"),
            ("move", "command:move"),
            ("designate_hunt", "command:designate_hunt"),
        ),
    ),
    CapabilitySpec(
        "utility",
        "Allow or forbid visible items and designate visible plants for cutting or harvesting.",
        (
            ("allow", "command:allow"),
            ("forbid", "command:forbid"),
            ("allow_all", "command:allow_all"),
            ("designate_cut", "command:designate_cut"),
            ("designate_harvest", "command:designate_harvest"),
        ),
    ),
)


class ToolRegistry:
    def __init__(self, schemas: list[dict[str, Any]] | None = None) -> None:
        schema_list = schemas if schemas is not None else TOOLS
        schemas_by_name: dict[str, dict[str, Any]] = {}
        for schema in schema_list:
            name = str(schema.get("name") or "")
            if not name:
                raise ValueError("Every model tool schema must have a name")
            if name in schemas_by_name:
                raise ValueError(f"Duplicate model tool name: {name}")
            schemas_by_name[name] = copy.deepcopy(schema)

        self._groups = {spec.name: spec for spec in CAPABILITY_SPECS}
        self._registrations: dict[str, ToolRegistration] = {}
        for spec in CAPABILITY_SPECS:
            for name, executor in spec.tools:
                if name in self._registrations:
                    raise ValueError(f"Tool belongs to multiple capability groups: {name}")
                schema = schemas_by_name.pop(name, None)
                if schema is None:
                    raise ValueError(f"Capability registration has no schema: {name}")
                if not executor:
                    raise ValueError(f"Tool registration has no executor: {name}")
                executor_kind, separator, executor_name = executor.partition(":")
                if separator != ":" or executor_kind not in ("read", "command", "controller"):
                    raise ValueError(f"Tool registration has invalid executor: {name} -> {executor}")
                if executor_name != name:
                    raise ValueError(f"Tool registration executor does not match tool name: {name} -> {executor}")
                self._registrations[name] = ToolRegistration(name, spec.name, schema, executor)
        if schemas_by_name:
            raise ValueError("Tool schemas have no capability registration: " + ", ".join(sorted(schemas_by_name)))

    @property
    def group_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in CAPABILITY_SPECS)

    @property
    def non_core_group_names(self) -> tuple[str, ...]:
        return tuple(name for name in self.group_names if name != CORE_GROUP)

    def registration(self, name: str) -> ToolRegistration | None:
        return self._registrations.get(name)

    def all_registrations(self) -> tuple[ToolRegistration, ...]:
        return tuple(self._registrations[name] for name in self._ordered_tool_names(self.group_names))

    def schemas_for_groups(self, groups: tuple[str, ...] | list[str] | set[str]) -> list[dict[str, Any]]:
        selected = set(groups)
        unknown = selected.difference(self._groups)
        if unknown:
            raise ValueError("Unknown capability groups: " + ", ".join(sorted(unknown)))
        return [copy.deepcopy(self._registrations[name].schema) for name in self._ordered_tool_names(selected)]

    def schema_chars_for_groups(self, groups: tuple[str, ...] | list[str] | set[str]) -> int:
        return serialized_chars(self.schemas_for_groups(groups))

    def capability_list(self, enabled_groups: tuple[str, ...] | list[str] | set[str]) -> list[dict[str, Any]]:
        enabled = set(enabled_groups)
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "enabled": spec.name in enabled,
            }
            for spec in CAPABILITY_SPECS
            if spec.name != CORE_GROUP
        ]

    def _ordered_tool_names(self, groups: set[str] | tuple[str, ...] | list[str]) -> list[str]:
        selected = set(groups)
        return [name for spec in CAPABILITY_SPECS if spec.name in selected for name, _ in spec.tools]


class ActiveToolSet:
    def __init__(self, registry: ToolRegistry, max_dynamic_groups: int = DEFAULT_MAX_ACTIVE_TOOL_GROUPS) -> None:
        if max_dynamic_groups < 1:
            raise ValueError("max_dynamic_groups must be at least 1")
        self.registry = registry
        self.max_dynamic_groups = max_dynamic_groups
        self._dynamic_groups: list[str] = []

    @property
    def groups(self) -> tuple[str, ...]:
        selected = {CORE_GROUP, *self._dynamic_groups}
        return tuple(name for name in self.registry.group_names if name in selected)

    @property
    def dynamic_groups(self) -> tuple[str, ...]:
        return tuple(self._dynamic_groups)

    def reset(self, preloaded: tuple[str, ...] | list[str] = ()) -> None:
        self._dynamic_groups = []
        for group in preloaded:
            result = self.enable(group)
            if result.get("success") is not True:
                raise ValueError(str(result.get("reason") or "Could not preload capability"))

    def enable(self, group: str) -> dict[str, Any]:
        if group in ("*", "all", "everything", CORE_GROUP):
            return {
                "success": False,
                "reason": "capabilityMustBeExplicitNonCoreGroup",
                "availableCapabilities": list(self.registry.non_core_group_names),
            }
        if group not in self.registry.non_core_group_names:
            return {
                "success": False,
                "reason": "unknownCapability",
                "availableCapabilities": list(self.registry.non_core_group_names),
            }
        if group in self._dynamic_groups:
            return self._enabled_result(group, 0, already_enabled=True)
        if len(self._dynamic_groups) >= self.max_dynamic_groups:
            return {
                "success": False,
                "reason": "activeCapabilityLimitReached",
                "maxDynamicGroups": self.max_dynamic_groups,
                "activeGroups": list(self.groups),
            }
        before = len(self.schemas())
        self._dynamic_groups.append(group)
        return self._enabled_result(group, len(self.schemas()) - before, already_enabled=False)

    def is_active(self, tool_name: str) -> bool:
        registration = self.registry.registration(tool_name)
        return registration is not None and registration.group in self.groups

    def schemas(self) -> list[dict[str, Any]]:
        return self.registry.schemas_for_groups(self.groups)

    def _enabled_result(self, group: str, tools_added: int, *, already_enabled: bool) -> dict[str, Any]:
        return {
            "success": True,
            "enabled": group,
            "alreadyEnabled": already_enabled,
            "toolsAdded": tools_added,
            "activeGroups": list(self.groups),
            "toolCount": len(self.schemas()),
            "schemaChars": serialized_chars(self.schemas()),
        }


def select_initial_tool_groups(decision_context: dict[str, Any] | None) -> tuple[str, ...]:
    """Conservatively preload only unambiguous immediate-response capabilities."""
    if not isinstance(decision_context, dict):
        return ()
    summary = decision_context.get("currentSummary")
    threats = summary.get("threats") if isinstance(summary, dict) else None
    if isinstance(threats, dict):
        active = threats.get("active")
        if threats.get("status") == "active" or (isinstance(active, (int, float)) and active > 0):
            return ("combat",)
    return ()


DEFAULT_TOOL_REGISTRY = ToolRegistry()
