import os
from typing import Optional

import psycopg2
from dotenv import load_dotenv

from app.models import CareerFeedbackRequest, CareerFeedbackResponse

load_dotenv()


class FeedbackService:
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")

    def save_feedback(
        self,
        request: CareerFeedbackRequest
    ) -> CareerFeedbackResponse:
        if not self.database_url:
            return CareerFeedbackResponse(
                success=False,
                feedback_id=None,
                message="DATABASE_URL is not configured."
            )

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor()

            cursor.execute(
                """
                insert into career_feedback (
                    career_analysis_id,
                    user_current_role,
                    detected_role_cluster,
                    user_experience_years,
                    user_city,
                    user_goal,
                    role_mapping_rating,
                    salary_realism_rating,
                    target_roles_rating,
                    skill_recommendations_rating,
                    overall_rating,
                    would_pay,
                    feedback_comment
                )
                values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                returning id
                """,
                (
                    request.career_analysis_id,
                    self._clean_text(request.user_current_role),
                    self._clean_text(request.detected_role_cluster),
                    request.user_experience_years,
                    self._clean_text(request.user_city),
                    self._clean_text(request.user_goal),
                    self._clean_text(request.role_mapping_rating),
                    self._clean_text(request.salary_realism_rating),
                    self._clean_text(request.target_roles_rating),
                    self._clean_text(request.skill_recommendations_rating),
                    request.overall_rating,
                    self._clean_text(request.would_pay),
                    self._clean_text(request.feedback_comment),
                )
            )

            feedback_id = cursor.fetchone()[0]

            connection.commit()
            cursor.close()
            connection.close()

            return CareerFeedbackResponse(
                success=True,
                feedback_id=feedback_id,
                message="Feedback saved successfully."
            )

        except Exception as e:
            return CareerFeedbackResponse(
                success=False,
                feedback_id=None,
                message=f"Failed to save feedback: {str(e)}"
            )

    def _clean_text(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        cleaned = value.strip()

        if not cleaned:
            return None

        return cleaned