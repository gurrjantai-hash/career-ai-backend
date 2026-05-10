from typing import Any, Dict, List

from app.models import CareerProfileRequest, CareerAnalysisResponse
from app.services.ai_service import AIService
from app.services.salary_service import SalaryService
from app.services.db_service import DBService
from app.prompts.career_prompts import career_analysis_prompt


class CareerService:

    def __init__(self):
        self.ai_service = AIService()
        self.salary_service = SalaryService()
        self.db_service = DBService()

    def analyze(self, profile: CareerProfileRequest) -> CareerAnalysisResponse:
        role_cluster = self.ai_service.classify_role_cluster(
            profile.current_role,
            profile.skills
        )

        salary = self.salary_service.calculate_salary(profile, role_cluster)

        prompt = career_analysis_prompt(profile, salary, role_cluster)

        ai_result = self.ai_service.get_json_response(prompt)

        response = CareerAnalysisResponse(
            role_cluster=role_cluster,
            current_level=self._get_string(ai_result, "current_level", "Not available"),

            summary=self._get_string(
                ai_result,
                "summary",
                "Career summary is not available for this analysis."
            ),
            recommended_next_move=self._get_string(
                ai_result,
                "recommended_next_move",
                "Recommended next move is not available for this analysis."
            ),
            goal_strategy=self._get_string(
                ai_result,
                "goal_strategy",
                f"The selected goal is {profile.goal}. The strategy should be aligned to this goal."
            ),

            salary_insight=salary,

            target_roles=self._get_list(ai_result, "target_roles"),
            top_skill_gaps=self._get_list(ai_result, "top_skill_gaps"),
            skill_salary_impact=self._get_dict(ai_result, "skill_salary_impact"),

            growth_paths=self._get_list(ai_result, "growth_paths"),
            why_recommendations=self._get_list(ai_result, "why_recommendations"),

            roadmap_4_weeks=self._get_dict(ai_result, "roadmap_4_weeks"),
            resume_suggestions=self._get_list(ai_result, "resume_suggestions"),

            confidence_notes=self._get_list(ai_result, "confidence_notes"),

            disclaimer="This is an AI-assisted estimate based on your profile and market patterns. It is not a guaranteed salary prediction."
        )

        self.db_service.save_career_analysis(profile, response)

        return response

    def _get_string(self, data: Dict[str, Any], key: str, default: str) -> str:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    def _get_list(self, data: Dict[str, Any], key: str) -> List[Any]:
        value = data.get(key)
        if isinstance(value, list):
            return value
        return []

    def _get_dict(self, data: Dict[str, Any], key: str) -> Dict[str, Any]:
        value = data.get(key)
        if isinstance(value, dict):
            return value
        return {}