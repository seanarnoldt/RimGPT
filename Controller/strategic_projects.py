"""Compact derived status for bounded strategic projects and parallel work."""

from __future__ import annotations

from typing import Any

from progress_tracking import authoritative_completion_for, loop_categories


PLANNING_HORIZON_DAYS = 3
MAX_PROJECT_CONTEXT = 3
MAX_TASK_CONTEXT = 8
MAX_ATTENTION_ITEMS = 8


def build_project_context(
    handoff: dict[str, Any] | None,
    progress: dict[str, Any],
    stall_metadata: dict[str, Any] | None,
    state: dict[str, Any],
    operational_risk: dict[str, Any],
) -> dict[str, Any]:
    projects = (handoff or {}).get("projects")
    if not isinstance(projects, list):
        projects = []
    stalled = {
        str(item.get("id")): item
        for item in (stall_metadata or {}).get("projectTasks", [])
        if isinstance(item, dict) and item.get("id")
    }
    progress_by_category = progress_categories(progress)
    result_projects = [
        summarize_project(project, stalled, progress_by_category, progress)
        for project in projects[:MAX_PROJECT_CONTEXT]
        if isinstance(project, dict)
    ]
    return {
        "planningHorizonDays": PLANNING_HORIZON_DAYS,
        "projects": result_projects,
        "workContinuity": build_work_continuity(state, operational_risk, result_projects),
    }


def summarize_project(
    project: dict[str, Any],
    stalled: dict[str, dict[str, Any]],
    progress_by_category: dict[str, list[str]],
    progress: dict[str, Any],
) -> dict[str, Any]:
    tasks = [item for item in project.get("tasks", [])[:MAX_TASK_CONTEXT] if isinstance(item, dict)]
    by_id = {str(item.get("id")): item for item in tasks if item.get("id")}
    completed = {task_id for task_id, item in by_id.items() if item.get("status") == "completed"}
    available = []
    active = []
    background = []
    waiting = []
    attention = []
    state_satisfied = []
    evidence: set[str] = set()

    for task_id, task in by_id.items():
        status = str(task.get("status") or "pending")
        dependencies = [str(item) for item in task.get("dependsOn", [])]
        waiting_on = [item for item in dependencies if item not in completed]
        blockers = [str(item) for item in task.get("blockers", []) if str(item)]
        task_stall = stalled.get(task_id, {})
        completion_evidence = authoritative_completion_for(task, progress)
        is_stalled = int_value(task_stall.get("noRelevantProgressCycles")) >= 2 and not completion_evidence
        if status == "completed":
            continue
        if completion_evidence:
            state_satisfied.append({
                "taskId": task_id,
                "evidence": completion_evidence,
                "guidance": "Authoritative state satisfies this phase; update the model-authored task status.",
            })
            for category in task_categories(task):
                evidence.update(progress_by_category.get(category, []))
            continue
        if task.get("mode") == "background" or status == "background":
            background.append(task_id)
        if status == "active":
            active.append(task_id)
        if waiting_on:
            waiting.append({"taskId": task_id, "waitingOn": waiting_on})
        elif status == "pending" and not blockers:
            available.append(task_id)
        if status == "blocked" or blockers:
            attention.append({"taskId": task_id, "reason": "blocked", "blockers": blockers})
        elif is_stalled:
            attention.append({
                "taskId": task_id,
                "reason": "stalled",
                "guidance": "Replan this task branch without dropping healthy parallel work.",
            })
        for category in task_categories(task):
            evidence.update(progress_by_category.get(category, []))

    return {
        "id": project.get("id"),
        "status": project.get("status"),
        "completedTaskIds": sorted(completed),
        "activeTaskIds": sorted(active),
        "backgroundTaskIds": sorted(background),
        "availableTaskIds": sorted(available),
        "waitingTasks": waiting[:MAX_ATTENTION_ITEMS],
        "attentionTasks": attention[:MAX_ATTENTION_ITEMS],
        "stateSatisfiedTasks": state_satisfied[:MAX_ATTENTION_ITEMS],
        "progressEvidence": sorted(evidence)[:8],
        "completion": {
            "completedTasks": len(completed),
            "stateSatisfiedTasks": len(state_satisfied),
            "totalTasks": len(tasks),
            "successCriteriaCount": len(project.get("successCriteria", [])),
            "requiresAuthoritativeCriteria": True,
        },
    }


def build_work_continuity(
    state: dict[str, Any],
    operational_risk: dict[str, Any],
    projects: list[dict[str, Any]],
) -> dict[str, Any]:
    background = []
    research = state.get("research") if isinstance(state.get("research"), dict) else {}
    current_research = research.get("current") if isinstance(research.get("current"), dict) else None
    if current_research is not None:
        background.append({
            "type": "research",
            "defName": current_research.get("defName"),
            "progress": current_research.get("progress"),
            "cost": current_research.get("cost"),
        })

    map_state = state.get("map") if isinstance(state.get("map"), dict) else {}
    growing_zones = [item for item in dict_list(map_state.get("zones")) if item.get("type") == "growing"]
    if growing_zones:
        complete = [item for item in growing_zones if item.get("plantingComplete") is True]
        background.append({
            "type": "growing",
            "zoneCount": len(growing_zones),
            "plantingCompleteZones": len(complete),
            "unfinishedPlantingZones": len(growing_zones) - len(complete),
            "growingCells": sum(int_value(item.get("growingCells")) for item in growing_zones),
            "harvestableCells": sum(int_value(item.get("harvestableCells")) for item in growing_zones),
            "states": sorted({str(item.get("growingState")) for item in growing_zones if item.get("growingState")}),
        })

    operations = state.get("operations") if isinstance(state.get("operations"), dict) else {}
    labor = operations.get("labor") if isinstance(operations.get("labor"), dict) else {}
    pending = labor.get("pendingWork") if isinstance(labor.get("pendingWork"), dict) else {}
    construction_count = int_value(pending.get("blueprints")) + int_value(pending.get("frames"))
    if construction_count:
        background.append({"type": "construction", "pending": construction_count})

    sleeping = sum(1 for pawn in dict_list(state.get("colonists")) if pawn_is_sleeping(pawn))
    if sleeping:
        background.append({"type": "normalSleep", "pawnCount": sleeping})

    attention = []
    risk_items = operational_risk.get("items") if isinstance(operational_risk.get("items"), list) else []
    urgent = [item for item in risk_items if item.get("triage") in ("urgent", "critical")]
    if urgent:
        attention.append({"type": "operationalRisk", "count": len(urgent)})
    threats = dict_list(state.get("threats"))
    if threats:
        attention.append({"type": "visibleThreats", "count": len(threats)})
    blockers = dict_list(labor.get("obviousBlockers"))
    if blockers:
        attention.append({"type": "laborBlockers", "count": len(blockers)})
    project_attention = sum(len(item.get("attentionTasks", [])) for item in projects)
    if project_attention:
        attention.append({"type": "projectTasks", "count": project_attention})

    capable_idle = int_value(labor.get("capableIdleColonistCount"))
    available_tasks = sum(len(item.get("availableTaskIds", [])) for item in projects)
    return {
        "background": background[:MAX_ATTENTION_ITEMS],
        "attention": attention[:MAX_ATTENTION_ITEMS],
        "capableIdleColonists": capable_idle,
        "availableProjectTasks": available_tasks,
        "parallelWorkRecommended": (
            not bool(operational_risk.get("interruptNormalPriorities"))
            and not threats
            and capable_idle > 0
        ),
    }


def progress_categories(progress: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for signal in progress.get("signals", []):
        if not isinstance(signal, dict):
            continue
        category = str(signal.get("category") or "")
        signal_type = str(signal.get("type") or "progress")
        if category and signal_type not in result.setdefault(category, []):
            result[category].append(signal_type)
    return result


def task_categories(task: dict[str, Any]) -> set[str]:
    objective = str(task.get("objective") or "")
    blockers = " ".join(str(item) for item in task.get("blockers", []))
    return loop_categories({"objective": objective, "nextAction": blockers})


def int_value(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def pawn_is_sleeping(pawn: dict[str, Any]) -> bool:
    job = pawn.get("currentJob")
    if not isinstance(job, dict):
        return False
    def_name = str(job.get("defName") or "").lower()
    return "laydown" in def_name or "sleep" in def_name
