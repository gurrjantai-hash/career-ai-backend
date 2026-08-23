from typing import Any, Dict, List

from app.models import CareerProfileRequest, CareerAnalysisResponse
from app.services.ai_service import AIService
from app.services.salary_service import SalaryService
from app.services.db_service import DBService
from app.services.role_intelligence_service import RoleIntelligenceService
from app.services.skill_premium_service import SkillPremiumService
from app.services.role_guardrail_service import RoleGuardrailService, RoleGuardrailDecision
from app.prompts.career_prompts import career_analysis_prompt


class CareerService:

    def __init__(self):
        self.ai_service = AIService()
        self.salary_service = SalaryService()
        self.db_service = DBService()
        self.role_intelligence_service = RoleIntelligenceService()
        self.skill_premium_service = SkillPremiumService()
        self.role_guardrail_service = RoleGuardrailService()

    def analyze(
        self,
        profile: CareerProfileRequest,
        user_id: str,
    ) -> CareerAnalysisResponse:
        role_intelligence = self.role_intelligence_service.map_role(profile)
        suggested_role_cluster = role_intelligence.primary_cluster

        if role_intelligence.confidence == "Low" and suggested_role_cluster == "General IT":
            ai_cluster = self.ai_service.classify_role_cluster(
                profile.current_role,
                profile.skills,
            )

            if ai_cluster:
                suggested_role_cluster = self._normalize_ai_cluster(ai_cluster)

        guardrail_decision = self.role_guardrail_service.build_decision(
            profile=profile,
            suggested_role_cluster=suggested_role_cluster,
        )
        role_cluster = guardrail_decision.final_role_cluster

        salary = self.salary_service.calculate_salary(
            profile=profile,
            role_cluster=role_cluster,
            role_intelligence=role_intelligence,
        )

        role_intelligence_context = self.role_intelligence_service.build_prompt_context(
            role_intelligence
        )

        prompt = career_analysis_prompt(
            profile=profile,
            salary=salary,
            role_cluster=role_cluster,
            role_intelligence_context=role_intelligence_context,
            role_guardrail_context=guardrail_decision.prompt_context,
            allowed_target_roles=guardrail_decision.allowed_target_roles,
            stretch_target_roles=guardrail_decision.stretch_target_roles,
            priority_skill_gaps=guardrail_decision.priority_skill_gaps,
        )

        ai_result = self.ai_service.get_json_response(prompt)

        guarded_result = self.role_guardrail_service.validate_ai_result(
            ai_result=ai_result,
            profile=profile,
            decision=guardrail_decision,
        )

        target_roles = guarded_result["target_roles"]
        top_skill_gaps = guarded_result["top_skill_gaps"]
        growth_paths = guarded_result["growth_paths"]

        target_salary_insights = self.salary_service.calculate_target_salary_insights(
            profile=profile,
            growth_paths=growth_paths,
            target_roles=target_roles,
        )

        skill_premium_insights = self.skill_premium_service.get_skill_premium_insights(
            role_cluster=role_cluster,
            top_skill_gaps=top_skill_gaps,
            target_roles=target_roles,
            limit=6,
        )

        response = CareerAnalysisResponse(
            role_cluster=role_cluster,
            current_level=self.role_guardrail_service.current_level_from_experience(
                profile.experience_years
            ),

            summary=guarded_result["summary"],
            recommended_next_move=guarded_result["recommended_next_move"],
            goal_strategy=guarded_result["goal_strategy"],

            salary_insight=salary,
            target_salary_insights=target_salary_insights,
            target_roles=target_roles,
            top_skill_gaps=top_skill_gaps,
            skill_salary_impact=guarded_result["skill_salary_impact"],
            skill_premium_insights=skill_premium_insights,

            growth_paths=growth_paths,
            why_recommendations=guarded_result["why_recommendations"],

            roadmap_4_weeks=guarded_result["roadmap_4_weeks"],
            resume_suggestions=guarded_result["resume_suggestions"],

            confidence_notes=self._add_role_intelligence_confidence_note(
                notes=guarded_result["confidence_notes"],
                role_intelligence=role_intelligence,
                decision=guardrail_decision,
            ),

            disclaimer="This is an AI-assisted estimate based on your profile and market patterns. It is not a guaranteed salary prediction.",
        )

        analysis_id = self.db_service.save_career_analysis(
            profile=profile,
            response=response,
            user_id=user_id,
        )

        if analysis_id:
            response.analysis_id = analysis_id

        return response

    def _add_role_intelligence_confidence_note(
        self,
        notes: List[str],
        role_intelligence,
        decision: RoleGuardrailDecision,
    ) -> List[str]:
        role_note = (
            f"Role mapping: your input role was matched to "
            f"'{role_intelligence.canonical_role}' under "
            f"'{role_intelligence.primary_cluster}' with "
            f"{role_intelligence.confidence.lower()} confidence."
        )

        guardrail_note = (
            f"Final career direction was validated by product guardrails as "
            f"'{decision.final_role_cluster}' with allowed target roles: "
            f"{', '.join(decision.allowed_target_roles)}."
        )

        merged = [role_note, guardrail_note]

        for note in notes:
            if isinstance(note, str) and note.strip():
                merged.append(note.strip())

        return self._dedupe_keep_order(merged)

    def _dedupe_keep_order(self, values: List[str]) -> List[str]:
        seen = set()
        result = []
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    def _normalize_ai_cluster(self, ai_cluster: str) -> str:
        normalized = ai_cluster.strip().lower()

        cluster_mapping = {
            "operations": "General IT",
            "it operations": "General IT",
            "software": "General IT",
            "technology": "General IT",
            "support": "Application Support",
            "application support": "Application Support",
            "production support": "Application Support",
            "qa": "Testing/QA",
            "testing": "Testing/QA",
            "quality assurance": "Testing/QA",
            "sdet": "Testing/QA",
            "frontend": "Frontend Engineering",
            "backend": "Backend Engineering",
            "full stack": "Full Stack Engineering",
            "devops": "DevOps",
            "cloud": "Cloud Engineering",
            "data": "Data Analytics",
            "business analysis": "Business Analysis",
            "business analyst": "Business Analysis",
            "security": "Cyber Security",
            "cyber security": "Cyber Security",
            "database": "Database",
            "dba": "Database",
            "mobile": "Mobile Engineering",
            "agile": "Agile Delivery",
            "project management": "Agile Delivery",
            "enterprise platform": "Enterprise Platforms",
        }

        return cluster_mapping.get(normalized, "General IT")
