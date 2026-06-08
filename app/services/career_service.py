from typing import Any, Dict, List

from app.models import CareerProfileRequest, CareerAnalysisResponse
from app.services.ai_service import AIService
from app.services.salary_service import SalaryService
from app.services.db_service import DBService
from app.services.role_intelligence_service import RoleIntelligenceService
from app.services.skill_premium_service import SkillPremiumService
from app.prompts.career_prompts import career_analysis_prompt


class CareerService:

    def __init__(self):
        self.ai_service = AIService()
        self.salary_service = SalaryService()
        self.db_service = DBService()
        self.role_intelligence_service = RoleIntelligenceService()
        self.skill_premium_service = SkillPremiumService()

    def analyze(self, profile: CareerProfileRequest) -> CareerAnalysisResponse:
        role_intelligence = self.role_intelligence_service.map_role(profile)

        role_cluster = role_intelligence.primary_cluster

        if role_intelligence.confidence == "Low" and role_cluster == "General IT":
            ai_cluster = self.ai_service.classify_role_cluster(
                profile.current_role,
                profile.skills
            )

            if ai_cluster:
                role_cluster = self._normalize_ai_cluster(ai_cluster)

        salary = self.salary_service.calculate_salary(
            profile=profile,
            role_cluster=role_cluster,
            role_intelligence=role_intelligence
        )

        role_intelligence_context = self.role_intelligence_service.build_prompt_context(
            role_intelligence
        )

        prompt = career_analysis_prompt(
            profile,
            salary,
            role_cluster,
            role_intelligence_context
        )

        ai_result = self.ai_service.get_json_response(prompt)

        growth_paths = self._get_list(ai_result, "growth_paths")
        target_roles = self._get_list(ai_result, "target_roles")
        top_skill_gaps = self._get_list(ai_result, "top_skill_gaps")

        target_salary_insights = self.salary_service.calculate_target_salary_insights(
            profile=profile,
            growth_paths=growth_paths,
            target_roles=target_roles,
        )

        skill_premium_insights = self.skill_premium_service.get_skill_premium_insights(
            role_cluster=role_cluster,
            top_skill_gaps=top_skill_gaps,
            target_roles=target_roles,
            limit=6
        )

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
            target_salary_insights=target_salary_insights,
            target_roles=target_roles,
            top_skill_gaps=top_skill_gaps,
            skill_salary_impact=self._get_dict(ai_result, "skill_salary_impact"),
            skill_premium_insights=skill_premium_insights,

            growth_paths=growth_paths,
            why_recommendations=self._get_list(ai_result, "why_recommendations"),

            roadmap_4_weeks=self._get_dict(ai_result, "roadmap_4_weeks"),
            resume_suggestions=self._get_list(ai_result, "resume_suggestions"),

            confidence_notes=self._add_role_intelligence_confidence_note(
                self._get_list(ai_result, "confidence_notes"),
                role_intelligence
            ),

            disclaimer="This is an AI-assisted estimate based on your profile and market patterns. It is not a guaranteed salary prediction."
        )

        self.db_service.save_career_analysis(profile, response)

        return response

    def _add_role_intelligence_confidence_note(
        self,
        notes: List[str],
        role_intelligence
    ) -> List[str]:
        role_note = (
            f"Role mapping: your input role was matched to "
            f"'{role_intelligence.canonical_role}' under "
            f"'{role_intelligence.primary_cluster}' with "
            f"{role_intelligence.confidence.lower()} confidence."
        )

        return [role_note] + notes

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

    def _normalize_ai_cluster(self, ai_cluster: str) -> str:
        normalized = ai_cluster.strip().lower()

        cluster_mapping = {
            "operations": "General IT",
            "it operations": "General IT",
            "software": "General IT",
            "technology": "General IT",
            "support": "Application Support",
            "application support": "Application Support",
            "production support": "Production Support",
            "qa": "Testing/QA",
            "testing": "Testing/QA",
            "quality assurance": "Testing/QA",
            "frontend": "Frontend Engineering",
            "backend": "Backend Engineering",
            "full stack": "Full Stack Engineering",
            "devops": "DevOps",
            "cloud": "Cloud Engineering",
            "data": "Data Analytics",
            "business analysis": "Business Analysis",
            "business analyst": "Business Analysis",
        }

        return cluster_mapping.get(normalized, "General IT")
