"""Bounded structured continuity between successful top-level decisions."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Callable


HANDOFF_SCHEMA_VERSION = 2
MAX_ASSESSMENT_CHARS = 600
MAX_OPEN_LOOPS = 6
MAX_OBJECTIVE_CHARS = 180
MAX_NEXT_ACTION_CHARS = 220
MAX_REASON_CHARS = 220
MAX_PROJECTS = 3
MAX_PROJECT_TASKS = 8
MAX_PROJECT_OBJECTIVE_CHARS = 160
MAX_PROJECT_RATIONALE_CHARS = 220
MAX_SUCCESS_CRITERIA = 4
MAX_CRITERION_CHARS = 140
MAX_PROJECT_BLOCKERS = 4
MAX_TASK_OBJECTIVE_CHARS = 160
MAX_TASK_BLOCKERS = 3
MAX_TASK_DEPENDENCIES = 4
MAX_HANDOFF_CHARS = 10_000
LOOP_STATUSES = {"pending", "blocked", "deferred"}
LOOP_RESOLUTIONS = {"completed", "cancelled", "invalidated"}
PROJECT_STATUSES = {"active", "background", "blocked"}
PROJECT_RESOLUTIONS = {"completed", "cancelled", "invalidated"}
TASK_STATUSES = {"pending", "active", "background", "blocked", "completed"}
TASK_MODES = {"active", "background"}
LOOP_ID_PATTERN = re.compile(r"^L-[0-9a-f]{6}(?:-[1-9][0-9]?)?$")
PROJECT_ID_PATTERN = re.compile(r"^P-[0-9a-f]{6}(?:-[1-9][0-9]?)?$")
TASK_ID_PATTERN = re.compile(r"^T-[0-9a-f]{6}(?:-[1-9][0-9]?)?$")
TASK_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class DecisionHandoffError(ValueError):
    pass


def prepare_handoff(
    arguments: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    logger: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise DecisionHandoffError("finish_decision arguments must be an object")
    prior = validate_handoff(previous) if previous is not None else empty_handoff()
    arguments = normalize_retained_project_criteria(arguments, prior, logger)
    assessment = bounded_text(arguments.get("assessment"), "assessment", MAX_ASSESSMENT_CHARS, required=False)
    open_values = arguments.get("open_loops")
    resolution_values = arguments.get("resolved_loops")
    if not isinstance(open_values, list) or not isinstance(resolution_values, list):
        raise DecisionHandoffError("open_loops and resolved_loops must be arrays")
    if len(open_values) > MAX_OPEN_LOOPS or len(resolution_values) > MAX_OPEN_LOOPS:
        raise DecisionHandoffError(f"at most {MAX_OPEN_LOOPS} open loops and resolutions are allowed")

    prior_by_id = {item["id"]: item for item in prior["openLoops"]}
    prior_by_objective = {normalize_text(item["objective"]): item["id"] for item in prior["openLoops"]}
    retained: set[str] = set()
    loops: list[dict[str, str]] = []
    used_ids: set[str] = set()

    for raw in open_values:
        if not isinstance(raw, dict):
            raise DecisionHandoffError("each open loop must be an object")
        objective = bounded_text(raw.get("objective"), "objective", MAX_OBJECTIVE_CHARS)
        next_action = bounded_text(raw.get("next_action"), "next_action", MAX_NEXT_ACTION_CHARS)
        status = str(raw.get("status") or "")
        if status not in LOOP_STATUSES:
            raise DecisionHandoffError("open-loop status must be pending, blocked, or deferred")
        reason = bounded_text(raw.get("reason"), "reason", MAX_REASON_CHARS, required=False)
        requested_id = raw.get("id")
        if requested_id is not None and not isinstance(requested_id, str):
            raise DecisionHandoffError("open-loop id must be a string or null")
        if requested_id:
            if requested_id not in prior_by_id:
                raise DecisionHandoffError(f"unknown prior open-loop id: {requested_id}")
            loop_id = requested_id
        else:
            loop_id = prior_by_objective.get(normalize_text(objective)) or make_loop_id(objective, used_ids | set(prior_by_id))
        if loop_id in used_ids:
            raise DecisionHandoffError(f"duplicate open-loop id: {loop_id}")
        if loop_id in prior_by_id:
            retained.add(loop_id)
        used_ids.add(loop_id)
        loops.append({
            "id": loop_id,
            "objective": objective,
            "nextAction": next_action,
            "status": status,
            "reason": reason,
        })

    resolved: set[str] = set()
    for raw in resolution_values:
        if not isinstance(raw, dict):
            raise DecisionHandoffError("each resolution must be an object")
        loop_id = str(raw.get("id") or "")
        resolution = str(raw.get("resolution") or "")
        if loop_id not in prior_by_id:
            raise DecisionHandoffError(f"unknown prior open-loop id: {loop_id}")
        if loop_id in retained or loop_id in resolved:
            raise DecisionHandoffError(f"open loop accounted for more than once: {loop_id}")
        if resolution not in LOOP_RESOLUTIONS:
            raise DecisionHandoffError("resolution must be completed, cancelled, or invalidated")
        bounded_text(raw.get("reason"), "resolution reason", MAX_REASON_CHARS, required=False)
        resolved.add(loop_id)

    missing = sorted(set(prior_by_id) - retained - resolved)
    if missing:
        raise DecisionHandoffError("prior open loops must be retained or resolved: " + ", ".join(missing))
    projects = prepare_projects(arguments, prior)
    handoff = {
        "schemaVersion": HANDOFF_SCHEMA_VERSION,
        "assessment": assessment,
        "openLoops": loops,
        "projects": projects,
    }
    return validate_handoff(handoff)


def normalize_retained_project_criteria(
    arguments: dict[str, Any],
    prior: dict[str, Any],
    logger: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Restore immutable criteria only for explicitly retained known projects."""
    normalized = copy.deepcopy(arguments)
    raw_projects = normalized.get("projects")
    if not isinstance(raw_projects, list):
        return normalized
    prior_by_id = {project["id"]: project for project in prior["projects"]}
    for raw in raw_projects:
        if not isinstance(raw, dict):
            continue
        project_id = raw.get("id")
        submitted = raw.get("success_criteria")
        persisted = prior_by_id.get(project_id) if isinstance(project_id, str) else None
        if persisted is None or not isinstance(submitted, list):
            continue
        try:
            bounded_text_list(
                submitted,
                "success_criteria",
                MAX_SUCCESS_CRITERIA,
                MAX_CRITERION_CHARS,
                minimum=1,
            )
        except DecisionHandoffError:
            # Malformed immutable input is not a harmless paraphrase; normal
            # project validation must report it rather than silently repair it.
            continue
        authoritative = copy.deepcopy(persisted["successCriteria"])
        if submitted != authoritative and logger is not None:
            logger(f"[HANDOFF] normalized immutable success criteria for retained project {project_id}")
        raw["success_criteria"] = authoritative
    return normalized


def fallback_handoff(previous: dict[str, Any] | None, assessment: str) -> dict[str, Any]:
    prior = validate_handoff(previous) if previous is not None else empty_handoff()
    result = copy.deepcopy(prior)
    fallback_assessment = truncate_text(assessment, MAX_ASSESSMENT_CHARS)
    if fallback_assessment:
        result["assessment"] = fallback_assessment
    return validate_handoff(result)


def validate_handoff(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionHandoffError("decision handoff has an invalid shape")
    version = value.get("schemaVersion")
    if version == 1 and set(value) == {"schemaVersion", "assessment", "openLoops"}:
        value = {**copy.deepcopy(value), "schemaVersion": HANDOFF_SCHEMA_VERSION, "projects": []}
    if set(value) != {"schemaVersion", "assessment", "openLoops", "projects"}:
        raise DecisionHandoffError("decision handoff has an invalid shape")
    if value.get("schemaVersion") != HANDOFF_SCHEMA_VERSION:
        raise DecisionHandoffError("unsupported decision handoff schemaVersion")
    assessment = bounded_text(value.get("assessment"), "assessment", MAX_ASSESSMENT_CHARS, required=False)
    raw_loops = value.get("openLoops")
    if not isinstance(raw_loops, list) or len(raw_loops) > MAX_OPEN_LOOPS:
        raise DecisionHandoffError(f"openLoops must contain at most {MAX_OPEN_LOOPS} items")
    loops: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_loops:
        if not isinstance(raw, dict) or set(raw) != {"id", "objective", "nextAction", "status", "reason"}:
            raise DecisionHandoffError("persisted open loop has an invalid shape")
        loop_id = str(raw.get("id") or "")
        if not LOOP_ID_PATTERN.fullmatch(loop_id) or loop_id in seen:
            raise DecisionHandoffError("persisted open loop has an invalid or duplicate id")
        status = str(raw.get("status") or "")
        if status not in LOOP_STATUSES:
            raise DecisionHandoffError("persisted open loop has an invalid status")
        seen.add(loop_id)
        loops.append({
            "id": loop_id,
            "objective": bounded_text(raw.get("objective"), "objective", MAX_OBJECTIVE_CHARS),
            "nextAction": bounded_text(raw.get("nextAction"), "nextAction", MAX_NEXT_ACTION_CHARS),
            "status": status,
            "reason": bounded_text(raw.get("reason"), "reason", MAX_REASON_CHARS, required=False),
        })
    projects = validate_projects(value.get("projects"))
    result = {
        "schemaVersion": HANDOFF_SCHEMA_VERSION,
        "assessment": assessment,
        "openLoops": loops,
        "projects": projects,
    }
    if handoff_chars(result) > MAX_HANDOFF_CHARS:
        raise DecisionHandoffError(f"decision handoff exceeds {MAX_HANDOFF_CHARS} characters")
    return result


def empty_handoff() -> dict[str, Any]:
    return {"schemaVersion": HANDOFF_SCHEMA_VERSION, "assessment": "", "openLoops": [], "projects": []}


def prepare_projects(arguments: dict[str, Any], prior: dict[str, Any]) -> list[dict[str, Any]]:
    if "projects" not in arguments and "resolved_projects" not in arguments:
        return copy.deepcopy(prior["projects"])
    raw_projects = arguments.get("projects")
    raw_resolved = arguments.get("resolved_projects")
    if not isinstance(raw_projects, list) or not isinstance(raw_resolved, list):
        raise DecisionHandoffError("projects and resolved_projects must be arrays")
    if len(raw_projects) > MAX_PROJECTS or len(raw_resolved) > MAX_PROJECTS:
        raise DecisionHandoffError(f"at most {MAX_PROJECTS} active and resolved projects are allowed")

    prior_by_id = {item["id"]: item for item in prior["projects"]}
    prior_by_objective = {normalize_text(item["objective"]): item["id"] for item in prior["projects"]}
    retained: set[str] = set()
    used_ids: set[str] = set()
    projects = []
    for raw in raw_projects:
        if not isinstance(raw, dict):
            raise DecisionHandoffError("each project must be an object")
        objective = bounded_text(raw.get("objective"), "project objective", MAX_PROJECT_OBJECTIVE_CHARS)
        requested_id = raw.get("id")
        if requested_id is not None and not isinstance(requested_id, str):
            raise DecisionHandoffError("project id must be a string or null")
        if requested_id:
            if requested_id not in prior_by_id:
                raise DecisionHandoffError(f"unknown prior project id: {requested_id}")
            project_id = requested_id
        else:
            project_id = prior_by_objective.get(normalize_text(objective)) or make_project_id(
                objective, used_ids | set(prior_by_id)
            )
        if project_id in used_ids:
            raise DecisionHandoffError(f"duplicate project id: {project_id}")
        previous = prior_by_id.get(project_id)
        if previous is not None:
            retained.add(project_id)
        used_ids.add(project_id)
        projects.append(prepare_project(raw, project_id, previous))

    resolved: set[str] = set()
    for raw in raw_resolved:
        if not isinstance(raw, dict):
            raise DecisionHandoffError("each project resolution must be an object")
        project_id = str(raw.get("id") or "")
        resolution = str(raw.get("resolution") or "")
        if project_id not in prior_by_id:
            raise DecisionHandoffError(f"unknown prior project id: {project_id}")
        if project_id in retained or project_id in resolved:
            raise DecisionHandoffError(f"project accounted for more than once: {project_id}")
        if resolution not in PROJECT_RESOLUTIONS:
            raise DecisionHandoffError("project resolution must be completed, cancelled, or invalidated")
        bounded_text(raw.get("reason"), "project resolution reason", MAX_REASON_CHARS, required=False)
        criteria_met = bounded_text_list(
            raw.get("criteria_met"), "criteria_met", MAX_SUCCESS_CRITERIA, MAX_CRITERION_CHARS
        )
        if resolution == "completed" and set(criteria_met) != set(prior_by_id[project_id]["successCriteria"]):
            raise DecisionHandoffError("completed project must explicitly confirm every persisted success criterion")
        resolved.add(project_id)

    missing = sorted(set(prior_by_id) - retained - resolved)
    if missing:
        raise DecisionHandoffError("prior projects must be retained or resolved: " + ", ".join(missing))
    return projects


def prepare_project(raw: dict[str, Any], project_id: str, previous: dict[str, Any] | None) -> dict[str, Any]:
    objective = bounded_text(raw.get("objective"), "project objective", MAX_PROJECT_OBJECTIVE_CHARS)
    rationale = bounded_text(raw.get("rationale"), "project rationale", MAX_PROJECT_RATIONALE_CHARS)
    project_status = str(raw.get("status") or "")
    if project_status not in PROJECT_STATUSES:
        raise DecisionHandoffError("project status must be active, background, or blocked")
    criteria = bounded_text_list(
        raw.get("success_criteria"), "success_criteria", MAX_SUCCESS_CRITERIA, MAX_CRITERION_CHARS,
        minimum=1,
    )
    if previous is not None and criteria != previous["successCriteria"]:
        raise DecisionHandoffError("an existing project's success criteria cannot be changed")
    blockers = bounded_text_list(raw.get("blockers"), "project blockers", MAX_PROJECT_BLOCKERS, MAX_REASON_CHARS)
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= MAX_PROJECT_TASKS:
        raise DecisionHandoffError(f"project tasks must contain 1 to {MAX_PROJECT_TASKS} items")

    prior_tasks = {item["id"]: item for item in (previous or {}).get("tasks", [])}
    prior_by_key = {item["key"]: item["id"] for item in prior_tasks.values()}
    task_ids: dict[str, str] = {}
    used_ids: set[str] = set()
    for task in raw_tasks:
        if not isinstance(task, dict):
            raise DecisionHandoffError("each project task must be an object")
        key = validate_task_key(task.get("key"))
        requested_id = task.get("id")
        if requested_id is not None and not isinstance(requested_id, str):
            raise DecisionHandoffError("task id must be a string or null")
        if requested_id:
            if requested_id not in prior_tasks:
                raise DecisionHandoffError(f"unknown prior task id: {requested_id}")
            task_id = requested_id
        else:
            task_id = prior_by_key.get(key) or make_task_id(project_id, key, used_ids | set(prior_tasks))
        if key in task_ids or task_id in used_ids:
            raise DecisionHandoffError("duplicate task key or id in project")
        task_ids[key] = task_id
        used_ids.add(task_id)

    missing_tasks = sorted(set(prior_tasks) - used_ids)
    if missing_tasks:
        raise DecisionHandoffError("prior project tasks must be retained and updated in place: " + ", ".join(missing_tasks))

    tasks = []
    for raw_task in raw_tasks:
        key = validate_task_key(raw_task.get("key"))
        task_id = task_ids[key]
        dependency_refs = raw_task.get("depends_on")
        if not isinstance(dependency_refs, list) or len(dependency_refs) > MAX_TASK_DEPENDENCIES:
            raise DecisionHandoffError(f"depends_on must contain at most {MAX_TASK_DEPENDENCIES} task references")
        dependencies = []
        for reference in dependency_refs:
            reference = str(reference or "")
            dependency_id = task_ids.get(reference, reference if reference in used_ids else None)
            if dependency_id is None:
                raise DecisionHandoffError(f"unknown task dependency: {reference}")
            if dependency_id == task_id:
                raise DecisionHandoffError("task cannot depend on itself")
            if dependency_id not in dependencies:
                dependencies.append(dependency_id)
        task_status = validate_enum(raw_task.get("status"), TASK_STATUSES, "task status")
        if task_id in prior_tasks and prior_tasks[task_id]["status"] == "completed" and task_status != "completed":
            raise DecisionHandoffError("a completed project task cannot return to an incomplete status")
        tasks.append({
            "id": task_id,
            "key": key,
            "objective": bounded_text(raw_task.get("objective"), "task objective", MAX_TASK_OBJECTIVE_CHARS),
            "status": task_status,
            "mode": validate_enum(raw_task.get("mode"), TASK_MODES, "task mode"),
            "dependsOn": dependencies,
            "blockers": bounded_text_list(
                raw_task.get("blockers"), "task blockers", MAX_TASK_BLOCKERS, MAX_REASON_CHARS
            ),
        })
    validate_dependency_cycles(tasks)
    return {
        "id": project_id,
        "objective": objective,
        "rationale": rationale,
        "status": project_status,
        "successCriteria": criteria,
        "blockers": blockers,
        "tasks": tasks,
    }


def validate_projects(raw_projects: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_projects, list) or len(raw_projects) > MAX_PROJECTS:
        raise DecisionHandoffError(f"projects must contain at most {MAX_PROJECTS} items")
    projects = []
    seen_projects: set[str] = set()
    for raw in raw_projects:
        if not isinstance(raw, dict) or set(raw) != {
            "id", "objective", "rationale", "status", "successCriteria", "blockers", "tasks"
        }:
            raise DecisionHandoffError("persisted project has an invalid shape")
        project_id = str(raw.get("id") or "")
        if not PROJECT_ID_PATTERN.fullmatch(project_id) or project_id in seen_projects:
            raise DecisionHandoffError("persisted project has an invalid or duplicate id")
        seen_projects.add(project_id)
        raw_tasks = raw.get("tasks")
        if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= MAX_PROJECT_TASKS:
            raise DecisionHandoffError(f"persisted project tasks must contain 1 to {MAX_PROJECT_TASKS} items")
        tasks = []
        seen_task_ids: set[str] = set()
        seen_keys: set[str] = set()
        for raw_task in raw_tasks:
            if not isinstance(raw_task, dict) or set(raw_task) != {
                "id", "key", "objective", "status", "mode", "dependsOn", "blockers"
            }:
                raise DecisionHandoffError("persisted project task has an invalid shape")
            task_id = str(raw_task.get("id") or "")
            key = validate_task_key(raw_task.get("key"))
            if not TASK_ID_PATTERN.fullmatch(task_id) or task_id in seen_task_ids or key in seen_keys:
                raise DecisionHandoffError("persisted project task has an invalid or duplicate id/key")
            seen_task_ids.add(task_id)
            seen_keys.add(key)
            tasks.append({
                "id": task_id,
                "key": key,
                "objective": bounded_text(raw_task.get("objective"), "task objective", MAX_TASK_OBJECTIVE_CHARS),
                "status": validate_enum(raw_task.get("status"), TASK_STATUSES, "task status"),
                "mode": validate_enum(raw_task.get("mode"), TASK_MODES, "task mode"),
                "dependsOn": validate_string_list(
                    raw_task.get("dependsOn"), "dependsOn", MAX_TASK_DEPENDENCIES, TASK_ID_PATTERN
                ),
                "blockers": bounded_text_list(
                    raw_task.get("blockers"), "task blockers", MAX_TASK_BLOCKERS, MAX_REASON_CHARS
                ),
            })
        if any(dependency not in seen_task_ids for task in tasks for dependency in task["dependsOn"]):
            raise DecisionHandoffError("persisted project task has an unknown dependency")
        validate_dependency_cycles(tasks)
        projects.append({
            "id": project_id,
            "objective": bounded_text(raw.get("objective"), "project objective", MAX_PROJECT_OBJECTIVE_CHARS),
            "rationale": bounded_text(raw.get("rationale"), "project rationale", MAX_PROJECT_RATIONALE_CHARS),
            "status": validate_enum(raw.get("status"), PROJECT_STATUSES, "project status"),
            "successCriteria": bounded_text_list(
                raw.get("successCriteria"), "successCriteria", MAX_SUCCESS_CRITERIA, MAX_CRITERION_CHARS,
                minimum=1,
            ),
            "blockers": bounded_text_list(raw.get("blockers"), "project blockers", MAX_PROJECT_BLOCKERS, MAX_REASON_CHARS),
            "tasks": tasks,
        })
    return projects


def handoff_chars(value: dict[str, Any] | None) -> int:
    return len(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))) if value else 0


def make_loop_id(objective: str, unavailable: set[str]) -> str:
    stem = "L-" + hashlib.sha256(normalize_text(objective).encode("utf-8")).hexdigest()[:6]
    candidate = stem
    suffix = 2
    while candidate in unavailable:
        candidate = f"{stem}-{suffix}"
        suffix += 1
    return candidate


def make_project_id(objective: str, unavailable: set[str]) -> str:
    return make_stable_id("P", normalize_text(objective), unavailable)


def make_task_id(project_id: str, key: str, unavailable: set[str]) -> str:
    return make_stable_id("T", f"{project_id}:{key}", unavailable)


def make_stable_id(prefix: str, seed: str, unavailable: set[str]) -> str:
    stem = prefix + "-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]
    candidate = stem
    suffix = 2
    while candidate in unavailable:
        candidate = f"{stem}-{suffix}"
        suffix += 1
    return candidate


def validate_task_key(value: Any) -> str:
    key = str(value or "").strip().lower()
    if not TASK_KEY_PATTERN.fullmatch(key):
        raise DecisionHandoffError("task key must use 1-40 lowercase letters, numbers, underscores, or hyphens")
    return key


def validate_enum(value: Any, allowed: set[str], field: str) -> str:
    result = str(value or "")
    if result not in allowed:
        raise DecisionHandoffError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return result


def bounded_text_list(
    value: Any,
    field: str,
    maximum_items: int,
    maximum_chars: int,
    *,
    minimum: int = 0,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum_items:
        raise DecisionHandoffError(f"{field} must contain {minimum} to {maximum_items} items")
    result = [bounded_text(item, field, maximum_chars) for item in value]
    if len(set(result)) != len(result):
        raise DecisionHandoffError(f"{field} cannot contain duplicates")
    return result


def validate_string_list(
    value: Any,
    field: str,
    maximum_items: int,
    pattern: re.Pattern[str],
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise DecisionHandoffError(f"{field} must contain at most {maximum_items} items")
    result = [str(item or "") for item in value]
    if any(not pattern.fullmatch(item) for item in result) or len(set(result)) != len(result):
        raise DecisionHandoffError(f"{field} contains an invalid or duplicate reference")
    return result


def validate_dependency_cycles(tasks: list[dict[str, Any]]) -> None:
    dependencies = {task["id"]: set(task["dependsOn"]) for task in tasks}
    visiting: set[str] = set()
    complete: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in complete:
            return
        if task_id in visiting:
            raise DecisionHandoffError("project task dependencies cannot contain a cycle")
        visiting.add(task_id)
        for dependency in dependencies.get(task_id, set()):
            visit(dependency)
        visiting.remove(task_id)
        complete.add(task_id)

    for task_id in dependencies:
        visit(task_id)


def bounded_text(value: Any, field: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise DecisionHandoffError(f"{field} must be a string")
    text = " ".join(value.split())
    if required and not text:
        raise DecisionHandoffError(f"{field} cannot be empty")
    if len(text) > maximum:
        raise DecisionHandoffError(f"{field} exceeds {maximum} characters")
    return text


def truncate_text(value: Any, maximum: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= maximum else text[: maximum - 3].rstrip() + "..."


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())
