from urllib import response

from app.models import CareerProfileRequest, CareerAnalysisResponse
from app.services.ai_service import AIService
from app.services.salary_service import SalaryService
from app.prompts.career_prompts import career_analysis_prompt
from app.services.db_service import DBService


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
            current_level=ai_result["current_level"],
            summary=ai_result["summary"],
            recommended_next_move=ai_result["recommended_next_move"],
            salary_insight=salary,
            target_roles=ai_result["target_roles"],
            top_skill_gaps=ai_result["top_skill_gaps"],
            skill_salary_impact=ai_result["skill_salary_impact"],
            roadmap_4_weeks=ai_result["roadmap_4_weeks"],
            resume_suggestions=ai_result["resume_suggestions"],
            confidence_notes=ai_result["confidence_notes"],
            disclaimer="This is an AI-assisted estimate based on your profile and market patterns. It is not a guaranteed salary prediction."
        )

        self.db_service.save_career_analysis(profile, response)

        return response