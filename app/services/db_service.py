import os
import json
import psycopg2
from dotenv import load_dotenv

from app.models import CareerProfileRequest, CareerAnalysisResponse

load_dotenv()


class DBService:
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")

    def save_career_analysis(
        self,
        profile: CareerProfileRequest,
        response: CareerAnalysisResponse
    ) -> None:
        if not self.database_url:
            print("DATABASE_URL not configured. Skipping DB save.")
            return

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor()

            insert_query = """
                insert into career_analyses (
                    currentrole,
                    experience_years,
                    current_salary_lpa,
                    city,
                    skills,
                    goal,
                    role_cluster,
                    current_level,
                    summary,
                    recommended_next_move,
                    market_min_lpa,
                    market_max_lpa,
                    salary_gap_lpa,
                    confidence,
                    target_roles,
                    top_skill_gaps,
                    skill_salary_impact,
                    roadmap_4_weeks,
                    resume_suggestions,
                    confidence_notes
                )
                values (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
            """

            cursor.execute(
                insert_query,
                (
                    profile.current_role,
                    profile.experience_years,
                    profile.current_salary_lpa,
                    profile.city,
                    profile.skills,
                    profile.goal,

                    response.role_cluster,
                    response.current_level,
                    response.summary,
                    response.recommended_next_move,

                    response.salary_insight.market_min_lpa,
                    response.salary_insight.market_max_lpa,
                    response.salary_insight.salary_gap_lpa,
                    response.salary_insight.confidence,

                    json.dumps(response.target_roles),
                    json.dumps(response.top_skill_gaps),
                    json.dumps(response.skill_salary_impact),
                    json.dumps(response.roadmap_4_weeks),
                    json.dumps(response.resume_suggestions),
                    json.dumps(response.confidence_notes),
                )
            )

            connection.commit()
            cursor.close()
            connection.close()

        except Exception as e:
            print(f"Failed to save career analysis: {e}")