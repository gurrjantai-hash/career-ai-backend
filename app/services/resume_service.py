from typing import Any, Dict, List

from app.models import (
    ResumeOptimizeRequest,
    ResumeOptimizeResponse,
    ResumeBulletImprovement,
)
from app.services.ai_service import AIService
from app.prompts.resume_prompts import resume_optimization_prompt


class ResumeService:

    def __init__(self):
        self.ai_service = AIService()

    def optimize_resume(
        self,
        request: ResumeOptimizeRequest
    ) -> ResumeOptimizeResponse:
        prompt = resume_optimization_prompt(request)

        ai_result = self.ai_service.get_json_response(prompt)

        improved_bullets = self._parse_bullets(
            ai_result.get("improved_bullets", [])
        )

        return ResumeOptimizeResponse(
            target_role=self._get_string(
                ai_result,
                "target_role",
                request.target_role
            ),
            resume_alignment=self._get_string(
                ai_result,
                "resume_alignment",
                "Medium"
            ),
            alignment_summary=self._get_string(
                ai_result,
                "alignment_summary",
                "Resume alignment summary is not available."
            ),
            improved_profile_summary=self._get_string(
                ai_result,
                "improved_profile_summary",
                "Improved profile summary is not available."
            ),
            improved_bullets=improved_bullets,
            missing_keywords=self._get_list(ai_result, "missing_keywords"),
            resume_improvement_priorities=self._get_list(
                ai_result,
                "resume_improvement_priorities"
            ),
            naukri_headline=self._get_string(
                ai_result,
                "naukri_headline",
                "Naukri headline is not available."
            ),
            linkedin_summary=self._get_string(
                ai_result,
                "linkedin_summary",
                "LinkedIn summary is not available."
            ),
            interview_positioning=self._get_list(
                ai_result,
                "interview_positioning"
            ),
            disclaimer="This is AI-generated resume guidance. Please review and edit before using it in real applications."
        )

    def _parse_bullets(self, bullets: Any) -> List[ResumeBulletImprovement]:
        if not isinstance(bullets, list):
            return []

        parsed_bullets = []

        for bullet in bullets:
            if not isinstance(bullet, dict):
                continue

            parsed_bullets.append(
                ResumeBulletImprovement(
                    original=str(
                        bullet.get("original", "Original bullet not provided")
                    ),
                    improved=str(
                        bullet.get("improved", "Improved bullet not provided")
                    ),
                    reason=str(
                        bullet.get("reason", "Reason not provided")
                    )
                )
            )

        return parsed_bullets

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