from typing import Any, Dict, List

from app.models import (
    LearningPlanRequest,
    LearningPlanResponse,
    ProjectSuggestion,
    ResourceRecommendation,
)
from app.services.ai_service import AIService
from app.prompts.learning_prompts import learning_plan_prompt


class LearningService:

    def __init__(self):
        self.ai_service = AIService()

    def generate_learning_plan(
        self,
        request: LearningPlanRequest
    ) -> LearningPlanResponse:
        prompt = learning_plan_prompt(request)

        ai_result = self.ai_service.get_json_response(prompt)

        project_suggestions = self._parse_project_suggestions(
            ai_result.get("project_suggestions", [])
        )

        resource_recommendations = self._parse_resource_recommendations(
            ai_result.get("resource_recommendations", [])
        )

        return LearningPlanResponse(
            target_role=self._get_string(
                ai_result,
                "target_role",
                request.target_role
            ),
            learning_goal=self._get_string(
                ai_result,
                "learning_goal",
                request.goal
            ),
            readiness_level=self._get_string(
                ai_result,
                "readiness_level",
                "Medium"
            ),
            readiness_summary=self._get_string(
                ai_result,
                "readiness_summary",
                "Learning readiness summary is not available."
            ),
            revision_topics=self._get_list(ai_result, "revision_topics"),
            new_skills_to_learn=self._get_list(ai_result, "new_skills_to_learn"),
            project_suggestions=project_suggestions,
            interview_prep_topics=self._get_list(ai_result, "interview_prep_topics"),
            resource_recommendations=resource_recommendations,
            weekly_learning_plan=self._get_dict(ai_result, "weekly_learning_plan"),
            job_readiness_checklist=self._get_list(ai_result, "job_readiness_checklist"),
            disclaimer="This is an AI-generated learning plan. Please adapt it based on your available time, current knowledge, and target job requirements."
        )

    def _parse_project_suggestions(self, projects: Any) -> List[ProjectSuggestion]:
        if not isinstance(projects, list):
            return []

        parsed_projects = []

        for project in projects:
            if not isinstance(project, dict):
                continue

            parsed_projects.append(
                ProjectSuggestion(
                    project_name=str(
                        project.get("project_name", "Project suggestion")
                    ),
                    description=str(
                        project.get("description", "Project description not available.")
                    ),
                    skills_covered=self._safe_string_list(
                        project.get("skills_covered", [])
                    ),
                    difficulty=str(
                        project.get("difficulty", "Medium")
                    ),
                    portfolio_value=str(
                        project.get("portfolio_value", "Portfolio value not available.")
                    )
                )
            )

        return parsed_projects

    def _parse_resource_recommendations(
        self,
        resources: Any
    ) -> List[ResourceRecommendation]:
        if not isinstance(resources, list):
            return []

        parsed_resources = []

        for resource in resources:
            if not isinstance(resource, dict):
                continue

            parsed_resources.append(
                ResourceRecommendation(
                    topic=str(resource.get("topic", "Learning topic")),
                    resource_type=str(
                        resource.get("resource_type", "YouTube / documentation / course")
                    ),
                    what_to_search=str(
                        resource.get("what_to_search", "Search for relevant learning material")
                    ),
                    expected_outcome=str(
                        resource.get("expected_outcome", "Understand and apply this topic")
                    )
                )
            )

        return parsed_resources

    def _get_string(self, data: Dict[str, Any], key: str, default: str) -> str:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    def _get_list(self, data: Dict[str, Any], key: str) -> List[str]:
        value = data.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    def _get_dict(self, data: Dict[str, Any], key: str) -> Dict[str, List[str]]:
        value = data.get(key)
        if isinstance(value, dict):
            return value
        return {}

    def _safe_string_list(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        return []