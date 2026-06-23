import os
import json
from typing import Optional

import psycopg2
from dotenv import load_dotenv
from fastapi.encoders import jsonable_encoder

from app.models import CareerProfileRequest, CareerAnalysisResponse

load_dotenv()


class DBService:
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")

    def save_career_analysis(
        self,
        profile: CareerProfileRequest,
        response: CareerAnalysisResponse
    ) -> Optional[str]:
        if not self.database_url:
            print("DATABASE_URL not configured. Skipping DB save.")
            return None

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor()

            full_analysis_json = jsonable_encoder(response)

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
                    goal_strategy,

                    market_min_lpa,
                    market_max_lpa,
                    salary_gap_lpa,
                    confidence,

                    target_roles,
                    top_skill_gaps,
                    skill_salary_impact,
                    growth_paths,
                    why_recommendations,
                    roadmap_4_weeks,
                    resume_suggestions,
                    confidence_notes,

                    salary_insight,
                    target_salary_insights,
                    skill_premium_insights,
                    disclaimer,
                    full_analysis_json
                )
                values (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                returning id
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
                    response.goal_strategy,

                    response.salary_insight.market_min_lpa,
                    response.salary_insight.market_max_lpa,
                    response.salary_insight.salary_gap_lpa,
                    response.salary_insight.confidence,

                    json.dumps(jsonable_encoder(response.target_roles)),
                    json.dumps(jsonable_encoder(response.top_skill_gaps)),
                    json.dumps(jsonable_encoder(response.skill_salary_impact)),
                    json.dumps(jsonable_encoder(response.growth_paths)),
                    json.dumps(jsonable_encoder(response.why_recommendations)),
                    json.dumps(jsonable_encoder(response.roadmap_4_weeks)),
                    json.dumps(jsonable_encoder(response.resume_suggestions)),
                    json.dumps(jsonable_encoder(response.confidence_notes)),

                    json.dumps(jsonable_encoder(response.salary_insight)),
                    json.dumps(jsonable_encoder(response.target_salary_insights)),
                    json.dumps(jsonable_encoder(response.skill_premium_insights)),
                    response.disclaimer,
                    json.dumps(full_analysis_json),
                )
            )

            inserted_row = cursor.fetchone()
            analysis_id = str(inserted_row[0]) if inserted_row else None

            connection.commit()
            cursor.close()
            connection.close()

            return analysis_id

        except Exception as e:
            print(f"Failed to save career analysis: {e}")
            return None