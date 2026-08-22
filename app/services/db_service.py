import os
import json
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fastapi.encoders import jsonable_encoder

from app.models import (
    CareerAnalysisResponse,
    CareerProfileRequest,
    CareerWorkspaceProfile,
    LatestCareerWorkspaceResponse,
)


load_dotenv()


class DBService:
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")

    def save_career_analysis(
        self,
        profile: CareerProfileRequest,
        response: CareerAnalysisResponse,
        user_id: str,
    ) -> Optional[str]:
        if not self.database_url:
            print("DATABASE_URL not configured. Skipping DB save.")
            return None

        if not user_id:
            print("Authenticated user_id is missing. Skipping DB save.")
            return None

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor()

            full_analysis_json = jsonable_encoder(response)

            insert_query = """
                insert into career_analyses (
                    user_id,
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
                    %s,
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
                    user_id,
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
                ),
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

    def get_latest_career_workspace(
        self,
        user_id: str,
    ) -> LatestCareerWorkspaceResponse:
        if not self.database_url:
            return LatestCareerWorkspaceResponse(
                success=False,
                has_analysis=False,
                message="DATABASE_URL is not configured.",
            )

        if not user_id:
            return LatestCareerWorkspaceResponse(
                success=False,
                has_analysis=False,
                message="Authenticated user_id is missing.",
            )

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select
                    id,
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
                    full_analysis_json,
                    created_at
                from career_analyses
                where user_id = %s
                order by created_at desc
                limit 1
                """,
                (user_id,),
            )

            row = cursor.fetchone()
            cursor.close()
            connection.close()

            if not row:
                return LatestCareerWorkspaceResponse(
                    success=True,
                    has_analysis=False,
                    message="No saved career analysis found for this user.",
                )

            profile = CareerWorkspaceProfile(
                current_role=row.get("currentrole") or "",
                experience_years=self._optional_float(row.get("experience_years")),
                current_salary_lpa=self._optional_float(row.get("current_salary_lpa")),
                city=row.get("city") or "",
                skills=self._safe_list(row.get("skills")),
                goal=row.get("goal") or "Increase salary",
            )

            analysis = self._build_career_analysis_response(row)

            return LatestCareerWorkspaceResponse(
                success=True,
                has_analysis=True,
                profile=profile,
                analysis=analysis,
                message="Latest career workspace loaded successfully.",
            )

        except Exception as e:
            print(f"Failed to load latest career workspace: {e}")
            return LatestCareerWorkspaceResponse(
                success=False,
                has_analysis=False,
                message=f"Failed to load latest career workspace: {str(e)}",
            )

    def _build_career_analysis_response(
        self,
        row: Dict[str, Any],
    ) -> CareerAnalysisResponse:
        full_analysis_json = self._safe_json(row.get("full_analysis_json"))

        if isinstance(full_analysis_json, dict):
            payload = dict(full_analysis_json)
            payload["analysis_id"] = str(row["id"])

            try:
                return CareerAnalysisResponse(**payload)
            except Exception as e:
                print(f"Failed to rebuild analysis from full_analysis_json: {e}")

        salary_insight = self._safe_dict(row.get("salary_insight"))
        if not salary_insight:
            salary_insight = {
                "current_salary_lpa": self._optional_float(row.get("current_salary_lpa")) or 0,
                "market_min_lpa": self._optional_float(row.get("market_min_lpa")) or 0,
                "market_max_lpa": self._optional_float(row.get("market_max_lpa")) or 0,
                "salary_gap_lpa": row.get("salary_gap_lpa") or "Not available",
                "confidence": row.get("confidence") or "Not available",
            }

        payload = {
            "analysis_id": str(row["id"]),
            "role_cluster": row.get("role_cluster") or "Unknown",
            "current_level": row.get("current_level") or "Not available",
            "summary": row.get("summary") or "Summary is not available for this analysis.",
            "recommended_next_move": row.get("recommended_next_move") or "Recommended next move is not available.",
            "goal_strategy": row.get("goal_strategy") or "Goal-specific strategy is not available.",
            "salary_insight": salary_insight,
            "target_salary_insights": self._safe_list(row.get("target_salary_insights")),
            "target_roles": self._safe_list(row.get("target_roles")),
            "top_skill_gaps": self._safe_list(row.get("top_skill_gaps")),
            "skill_salary_impact": self._safe_dict(row.get("skill_salary_impact")),
            "skill_premium_insights": self._safe_list(row.get("skill_premium_insights")),
            "growth_paths": self._safe_list(row.get("growth_paths")),
            "why_recommendations": self._safe_list(row.get("why_recommendations")),
            "roadmap_4_weeks": self._safe_dict(row.get("roadmap_4_weeks")),
            "resume_suggestions": self._safe_list(row.get("resume_suggestions")),
            "confidence_notes": self._safe_list(row.get("confidence_notes")),
            "disclaimer": row.get("disclaimer") or "This is an AI-assisted estimate and not a guaranteed salary prediction.",
        }

        return CareerAnalysisResponse(**payload)

    def _safe_json(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value

        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value

        return value

    def _safe_list(self, value: Any) -> List[Any]:
        parsed = self._safe_json(value)

        if isinstance(parsed, list):
            return parsed

        if isinstance(parsed, tuple):
            return list(parsed)

        if isinstance(parsed, str) and parsed.strip():
            return [item.strip() for item in parsed.split(",") if item.strip()]

        return []

    def _safe_dict(self, value: Any) -> Dict[str, Any]:
        parsed = self._safe_json(value)

        if isinstance(parsed, dict):
            return parsed

        return {}

    def _optional_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None

        try:
            return float(value)
        except Exception:
            return None
