import os
from typing import Any, Dict, List

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()


class SkillPremiumService:
    """
    Phase 2D Skill Premium Service.

    Purpose:
    - Rank missing skills by salary/career premium
    - Use DB-backed skill_premium_matrix
    - Help roadmap, learning plan, salary impact, and future course recommendations
    """

    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")

    def get_skill_premium_insights(
        self,
        role_cluster: str,
        top_skill_gaps: List[str],
        target_roles: List[str] | None = None,
        limit: int = 6
    ) -> List[Dict[str, Any]]:
        if not role_cluster or not top_skill_gaps:
            return []

        premium_rows = self._load_skill_premium_rows(role_cluster)

        if not premium_rows:
            return self._fallback_skill_premium(top_skill_gaps, limit)

        ranked_skills = []

        for skill in top_skill_gaps:
            matched_row = self._find_matching_premium_row(skill, premium_rows)

            if matched_row:
                ranked_skills.append(
                    {
                        "skill_name": skill,
                        "premium_score": float(matched_row.get("premium_score") or 5),
                        "market_relevance": matched_row.get("market_relevance") or "Medium",
                        "learning_difficulty": matched_row.get("learning_difficulty") or "Medium",
                        "proof_required": matched_row.get("proof_required") or "Build a small practical project or interview-ready example.",
                        "priority": self._priority_from_score(
                            float(matched_row.get("premium_score") or 5)
                        ),
                        "source": "skill_premium_matrix"
                    }
                )
            else:
                ranked_skills.append(
                    {
                        "skill_name": skill,
                        "premium_score": 5.0,
                        "market_relevance": "Medium",
                        "learning_difficulty": "Medium",
                        "proof_required": "Build a small practical project or prepare interview-ready examples.",
                        "priority": "Medium",
                        "source": "fallback"
                    }
                )

        ranked_skills.sort(
            key=lambda item: (
                item["premium_score"],
                self._difficulty_rank(item["learning_difficulty"])
            ),
            reverse=True
        )

        return ranked_skills[:limit]

    def _load_skill_premium_rows(self, role_cluster: str) -> List[Dict[str, Any]]:
        if not self.database_url:
            return []

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select
                    skill_name,
                    cluster,
                    premium_score,
                    market_relevance,
                    learning_difficulty,
                    proof_required
                from skill_premium_matrix
                where lower(cluster) = lower(%s)
                order by premium_score desc
                """,
                (role_cluster,)
            )

            rows = cursor.fetchall()

            cursor.close()
            connection.close()

            return [dict(row) for row in rows]

        except Exception as e:
            print(f"Failed to load skill premium rows for {role_cluster}: {e}")
            return []

    def _find_matching_premium_row(
        self,
        skill: str,
        premium_rows: List[Dict[str, Any]]
    ) -> Dict[str, Any] | None:
        normalized_skill = self._normalize_text(skill)

        for row in premium_rows:
            row_skill = self._normalize_text(str(row.get("skill_name", "")))

            if normalized_skill == row_skill:
                return row

        for row in premium_rows:
            row_skill = self._normalize_text(str(row.get("skill_name", "")))

            if normalized_skill in row_skill or row_skill in normalized_skill:
                return row

        return None

    def _fallback_skill_premium(
        self,
        top_skill_gaps: List[str],
        limit: int
    ) -> List[Dict[str, Any]]:
        result = []

        for skill in top_skill_gaps[:limit]:
            result.append(
                {
                    "skill_name": skill,
                    "premium_score": 5.0,
                    "market_relevance": "Medium",
                    "learning_difficulty": "Medium",
                    "proof_required": "Build a small practical project or prepare interview-ready examples.",
                    "priority": "Medium",
                    "source": "fallback"
                }
            )

        return result

    def _priority_from_score(self, score: float) -> str:
        if score >= 8:
            return "High"
        if score >= 6:
            return "Medium"
        return "Low"

    def _difficulty_rank(self, difficulty: str) -> int:
        normalized = self._normalize_text(difficulty)

        if "hard" in normalized:
            return 3
        if "medium" in normalized:
            return 2
        if "easy" in normalized:
            return 1

        return 2

    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""

        return (
            text.lower()
            .replace("/", " ")
            .replace("-", " ")
            .replace("_", " ")
            .replace("&", " and ")
            .strip()
        )