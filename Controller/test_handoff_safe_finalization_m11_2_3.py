import unittest

from agent_controller import SYSTEM_INSTRUCTIONS
from decision_handoff import DecisionHandoffError, prepare_handoff
from prompt_runtime import RIMGPT_PROMPT_VERSION
from test_strategic_projects_m10g import finish_arguments, project_input, retained_project, task
from tools import TOOLS


RICE_CRITERIA = [
    "A dedicated rice growing zone exists",
    "The rice zone is substantially planted",
]


def rice_project(project_id=None):
    return project_input(
        project_id,
        tasks=[
            task("plant_rice", "Establish and sow the rice field"),
            task("food_buffer", "Build a stable food buffer", depends_on=["plant_rice"]),
        ],
        criteria=RICE_CRITERIA,
    )


class HandoffSafeFinalizationM1123Tests(unittest.TestCase):
    def setUp(self):
        self.prior = prepare_handoff(finish_arguments([rice_project()]), None)
        self.project = self.prior["projects"][0]

    def test_paid_rephrasing_is_normalized_without_false_completion(self):
        logs = []
        submitted = retained_project(
            self.project,
            task_updates={
                "plant_rice": {
                    "status": "blocked",
                    "blockers": ["The selected field still needs sowing labor"],
                },
                "food_buffer": {"status": "background"},
            },
            status="blocked",
        )
        submitted["success_criteria"] = [
            "Adequate crop production exists",
            "The colony has a sustainable food supply",
        ]
        submitted["blockers"] = ["Rice sowing has not completed"]

        result = prepare_handoff(finish_arguments([submitted]), self.prior, logger=logs.append)
        retained = result["projects"][0]

        self.assertEqual(retained["successCriteria"], RICE_CRITERIA)
        self.assertEqual(retained["status"], "blocked")
        self.assertEqual(retained["blockers"], ["Rice sowing has not completed"])
        statuses = {item["key"]: item["status"] for item in retained["tasks"]}
        self.assertEqual(statuses, {"plant_rice": "blocked", "food_buffer": "background"})
        self.assertNotEqual(retained["status"], "completed")
        self.assertEqual(
            logs,
            [f"[HANDOFF] normalized immutable success criteria for retained project {self.project['id']}"],
        )

    def test_new_project_keeps_submitted_criteria(self):
        criteria = ["A new stonecutting bench is operational"]
        new_project = project_input(
            tasks=[task("stonecutting", "Build a stonecutting bench")],
            criteria=criteria,
        )

        result = prepare_handoff(finish_arguments([new_project]), None)

        self.assertEqual(result["projects"][0]["successCriteria"], criteria)

    def test_completed_project_criteria_met_is_not_normalized(self):
        resolution = {
            "id": self.project["id"],
            "resolution": "completed",
            "reason": "Food production is established",
            "criteria_met": ["Adequate crop production exists"],
        }

        with self.assertRaisesRegex(DecisionHandoffError, "every persisted success criterion"):
            prepare_handoff(finish_arguments([], [resolution]), self.prior)

    def test_unknown_project_id_is_not_repaired(self):
        submitted = rice_project("P-abcdef")
        submitted["success_criteria"] = ["Generalized food production exists"]

        with self.assertRaisesRegex(DecisionHandoffError, "unknown prior project id"):
            prepare_handoff(finish_arguments([submitted]), self.prior)

    def test_malformed_retained_criteria_are_not_repaired(self):
        submitted = retained_project(self.project)
        submitted["success_criteria"] = []

        with self.assertRaisesRegex(DecisionHandoffError, "success_criteria must contain"):
            prepare_handoff(finish_arguments([submitted]), self.prior)

    def test_missing_prior_project_and_task_still_fail(self):
        with self.assertRaisesRegex(DecisionHandoffError, "prior projects must be retained or resolved"):
            prepare_handoff(finish_arguments([]), self.prior)

        missing_task = retained_project(self.project)
        missing_task["tasks"] = missing_task["tasks"][:-1]
        missing_task["success_criteria"] = ["Generalized food production exists"]
        with self.assertRaisesRegex(DecisionHandoffError, "prior project tasks must be retained"):
            prepare_handoff(finish_arguments([missing_task]), self.prior)

    def test_completed_task_cannot_regress_after_criteria_normalization(self):
        completed_input = retained_project(
            self.project, task_updates={"plant_rice": {"status": "completed"}}
        )
        completed = prepare_handoff(finish_arguments([completed_input]), self.prior)
        regressed = retained_project(
            completed["projects"][0], task_updates={"plant_rice": {"status": "pending"}}
        )
        regressed["success_criteria"] = ["Generalized food production exists"]

        with self.assertRaisesRegex(DecisionHandoffError, "completed project task"):
            prepare_handoff(finish_arguments([regressed]), completed)

    def test_invalid_dependency_change_is_not_repaired(self):
        submitted = retained_project(
            self.project,
            task_updates={"food_buffer": {"depends_on": ["unknown_task"]}},
        )
        submitted["success_criteria"] = ["Generalized food production exists"]

        with self.assertRaisesRegex(DecisionHandoffError, "unknown task dependency"):
            prepare_handoff(finish_arguments([submitted]), self.prior)

    def test_prompt_and_schema_require_exact_retained_criteria(self):
        self.assertEqual(RIMGPT_PROMPT_VERSION, "context-memory-v1-m11.2.3")
        self.assertIn("copy success_criteria exactly as persisted", SYSTEM_INSTRUCTIONS)
        finish = next(tool for tool in TOOLS if tool["name"] == "finish_decision")
        project_schema = finish["parameters"]["properties"]["projects"]
        self.assertIn("copy success_criteria exactly as persisted", project_schema["description"])
        criterion_description = project_schema["items"]["properties"]["success_criteria"]["description"]
        self.assertIn("exactly copy persisted criteria", criterion_description)


if __name__ == "__main__":
    unittest.main()
