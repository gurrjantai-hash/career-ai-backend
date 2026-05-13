import os
import json
import re
from typing import Any, Dict, List

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from app.models import CareerProfileRequest, RoleIntelligenceResult

load_dotenv()


class RoleIntelligenceService:
    """
    Phase 2A Role Intelligence Service.

    This service maps a user's free-text role + skills to a known canonical IT role.

    It supports broad IT career families:
    - Software development
    - QA/testing
    - Automation testing
    - SDET
    - Application support
    - Production support
    - Cloud support
    - DevOps
    - Data roles
    - Business analyst
    - Database roles
    - Cyber security
    """

    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")

    def map_role(self, profile: CareerProfileRequest) -> RoleIntelligenceResult:
        canonical_roles = self._load_canonical_roles()

        if not canonical_roles:
            return self._fallback_result(profile)

        best_role = None
        best_score = 0.0
        best_matched_skills: List[str] = []
        best_missing_core_skills_from_canonical: List[str] = []

        for role in canonical_roles:
            score_result = self._score_role_match(profile, role)

            if score_result["score"] > best_score:
                best_score = score_result["score"]
                best_role = role
                best_matched_skills = score_result["matched_skills"]
                best_missing_core_skills_from_canonical = score_result[
                    "missing_core_skills"
                ]

        if not best_role:
            return self._fallback_result(profile)

        canonical_role_name = str(best_role.get("role_name", profile.current_role))

        role_skill_rows = self._load_role_skill_matrix(canonical_role_name)

        skill_gap_result = self._calculate_skill_gaps_from_matrix(
            profile=profile,
            role_skill_rows=role_skill_rows,
            fallback_matched_skills=best_matched_skills,
            fallback_missing_core_skills=best_missing_core_skills_from_canonical,
        )

        confidence = self._calculate_confidence(best_score)

        return RoleIntelligenceResult(
            input_role=profile.current_role,
            canonical_role=canonical_role_name,
            role_family=str(best_role.get("role_family", "General IT")),
            primary_cluster=str(best_role.get("primary_cluster", "General IT")),
            secondary_clusters=self._safe_list(best_role.get("secondary_clusters")),

            matched_skills=skill_gap_result["matched_skills"],
            missing_core_skills=skill_gap_result["missing_core_skills"],
            missing_growth_skills=skill_gap_result["missing_growth_skills"],
            high_priority_missing_skills=skill_gap_result[
                "high_priority_missing_skills"
            ],

            adjacent_paths=self._safe_list(best_role.get("adjacent_paths")),
            confidence=confidence,
            match_score=round(best_score, 2),
        )

    def build_prompt_context(self, result: RoleIntelligenceResult) -> str:
        """
        This context is injected into the main career analysis prompt.
        It helps AI produce grounded and role-family-aware recommendations.
        """

        return f"""
ROLE INTELLIGENCE CONTEXT:
- User input role: {result.input_role}
- Canonical matched role: {result.canonical_role}
- Role family: {result.role_family}
- Primary cluster: {result.primary_cluster}
- Secondary clusters: {result.secondary_clusters}
- Matched skills from user profile: {result.matched_skills}
- Missing core skills for this role: {result.missing_core_skills}
- Missing growth skills for career progression: {result.missing_growth_skills}
- High-priority missing skills: {result.high_priority_missing_skills}
- Possible adjacent career paths: {result.adjacent_paths}
- Role match confidence: {result.confidence}
- Role match score: {result.match_score}

IMPORTANT:
Use this role intelligence context to make recommendations more accurate.
Do not assume every IT user is a backend developer.
If the user is from support, QA, testing, business analysis, data, security, cloud, or infrastructure, recommend paths suitable for that role family.
Prioritize high-priority missing skills when creating skill gaps, roadmap, learning plan direction, and resume suggestions.
"""

    def _load_canonical_roles(self) -> List[Dict[str, Any]]:
        if not self.database_url:
            print("DATABASE_URL not configured. Role intelligence fallback will be used.")
            return []

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select
                    role_name,
                    role_family,
                    primary_cluster,
                    secondary_clusters,
                    common_titles,
                    core_skills,
                    adjacent_paths,
                    typical_goals
                from canonical_roles
                """
            )

            rows = cursor.fetchall()

            cursor.close()
            connection.close()

            return [dict(row) for row in rows]

        except Exception as e:
            print(f"Failed to load canonical roles: {e}")
            return []

    def _load_role_skill_matrix(self, role_name: str) -> List[Dict[str, Any]]:
        if not self.database_url:
            return []

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select
                    role_name,
                    skill_name,
                    importance_score,
                    skill_type,
                    level
                from role_skill_matrix
                where lower(role_name) = lower(%s)
                order by importance_score desc
                """,
                (role_name,)
            )

            rows = cursor.fetchall()

            cursor.close()
            connection.close()

            return [dict(row) for row in rows]

        except Exception as e:
            print(f"Failed to load role skill matrix for {role_name}: {e}")
            return []

    def _score_role_match(
        self,
        profile: CareerProfileRequest,
        role: Dict[str, Any]
    ) -> Dict[str, Any]:
        input_role = self._normalize_text(profile.current_role)
        input_skills = [self._normalize_text(skill) for skill in profile.skills]

        role_name = self._normalize_text(str(role.get("role_name", "")))
        role_family = self._normalize_text(str(role.get("role_family", "")))
        primary_cluster = self._normalize_text(str(role.get("primary_cluster", "")))

        common_titles = [
            self._normalize_text(title)
            for title in self._safe_list(role.get("common_titles"))
        ]

        core_skills_raw = self._safe_list(role.get("core_skills"))
        core_skills_normalized = [
            self._normalize_text(skill)
            for skill in core_skills_raw
        ]

        score = 0.0

        # 1. Exact or near title match has highest weight.
        if input_role == role_name:
            score += 45

        if role_name and role_name in input_role:
            score += 35

        for title in common_titles:
            if not title:
                continue

            if input_role == title:
                score += 45
            elif title in input_role or input_role in title:
                score += 30
            elif self._token_overlap_score(input_role, title) >= 0.6:
                score += 20

        # 2. Cluster/family token matching.
        if primary_cluster and self._token_overlap_score(input_role, primary_cluster) >= 0.5:
            score += 10

        if role_family and self._token_overlap_score(input_role, role_family) >= 0.5:
            score += 8

        # 3. Canonical core skill match.
        matched_skills = []
        missing_core_skills = []

        for original_skill, normalized_skill in zip(
            core_skills_raw,
            core_skills_normalized
        ):
            skill_matched = False

            for input_skill in input_skills:
                if not input_skill:
                    continue

                if normalized_skill == input_skill:
                    skill_matched = True
                elif normalized_skill in input_skill or input_skill in normalized_skill:
                    skill_matched = True
                elif self._token_overlap_score(normalized_skill, input_skill) >= 0.75:
                    skill_matched = True

            if skill_matched:
                matched_skills.append(str(original_skill))
                score += 7
            else:
                missing_core_skills.append(str(original_skill))

        # 4. Important keyword boosting for broad IT families.
        score += self._keyword_boost(input_role, input_skills, role)

        score = min(score, 100)

        return {
            "score": score,
            "matched_skills": matched_skills,
            "missing_core_skills": missing_core_skills[:6],
        }

    def _calculate_skill_gaps_from_matrix(
        self,
        profile: CareerProfileRequest,
        role_skill_rows: List[Dict[str, Any]],
        fallback_matched_skills: List[str],
        fallback_missing_core_skills: List[str],
    ) -> Dict[str, List[str]]:
        """
        Uses role_skill_matrix to calculate skill gaps.

        If matrix is missing for a role, fallback to skills from canonical_roles.
        """

        if not role_skill_rows:
            return {
                "matched_skills": fallback_matched_skills,
                "missing_core_skills": fallback_missing_core_skills,
                "missing_growth_skills": [],
                "high_priority_missing_skills": fallback_missing_core_skills[:5],
            }

        user_skills_normalized = [
            self._normalize_text(skill)
            for skill in profile.skills
        ]

        matched_skills: List[str] = []
        missing_core_skills: List[str] = []
        missing_growth_skills: List[str] = []
        missing_skills_with_score: List[Dict[str, Any]] = []

        for row in role_skill_rows:
            skill_name = str(row.get("skill_name", ""))
            normalized_skill_name = self._normalize_text(skill_name)
            skill_type = str(row.get("skill_type", "")).lower()
            importance_score = float(row.get("importance_score") or 5)

            is_matched = self._is_skill_matched(
                normalized_skill_name,
                user_skills_normalized
            )

            if is_matched:
                matched_skills.append(skill_name)
            else:
                if "core" in skill_type:
                    missing_core_skills.append(skill_name)
                elif "growth" in skill_type:
                    missing_growth_skills.append(skill_name)
                else:
                    # If skill type is unclear, treat high-importance missing skill as core.
                    if importance_score >= 8:
                        missing_core_skills.append(skill_name)
                    else:
                        missing_growth_skills.append(skill_name)

                missing_skills_with_score.append(
                    {
                        "skill_name": skill_name,
                        "importance_score": importance_score,
                        "skill_type": skill_type,
                    }
                )

        missing_skills_with_score.sort(
            key=lambda item: item["importance_score"],
            reverse=True
        )

        high_priority_missing_skills = [
            item["skill_name"]
            for item in missing_skills_with_score[:6]
        ]

        return {
            "matched_skills": self._dedupe_keep_order(matched_skills),
            "missing_core_skills": self._dedupe_keep_order(missing_core_skills)[:8],
            "missing_growth_skills": self._dedupe_keep_order(missing_growth_skills)[:8],
            "high_priority_missing_skills": self._dedupe_keep_order(
                high_priority_missing_skills
            )[:6],
        }

    def _is_skill_matched(
        self,
        normalized_required_skill: str,
        user_skills_normalized: List[str]
    ) -> bool:
        for user_skill in user_skills_normalized:
            if not user_skill:
                continue

            if normalized_required_skill == user_skill:
                return True

            if normalized_required_skill in user_skill:
                return True

            if user_skill in normalized_required_skill:
                return True

            if self._token_overlap_score(normalized_required_skill, user_skill) >= 0.75:
                return True

        return False

    def _keyword_boost(
        self,
        input_role: str,
        input_skills: List[str],
        role: Dict[str, Any]
    ) -> float:
        role_name = self._normalize_text(str(role.get("role_name", "")))
        primary_cluster = self._normalize_text(str(role.get("primary_cluster", "")))
        combined = " ".join([input_role] + input_skills)

        boost = 0.0

        # Support roles
        if any(word in combined for word in ["support", "l2", "l3", "incident", "production"]):
            if any(word in role_name or word in primary_cluster for word in ["support", "production", "application support"]):
                boost += 18

        # QA/testing roles
        if any(word in combined for word in ["qa", "tester", "testing", "test cases", "manual"]):
            if any(word in role_name or word in primary_cluster for word in ["qa", "testing", "tester"]):
                boost += 18

        # Automation/SDET roles
        if any(word in combined for word in ["selenium", "testng", "automation", "cypress", "restassured"]):
            if any(word in role_name or word in primary_cluster for word in ["automation", "sdet"]):
                boost += 18

        # DevOps/cloud roles
        if any(word in combined for word in ["devops", "docker", "kubernetes", "terraform", "ci cd", "cicd"]):
            if any(word in role_name or word in primary_cluster for word in ["devops", "cloud", "sre", "platform"]):
                boost += 18

        # Data roles
        if any(word in combined for word in ["data analyst", "power bi", "tableau", "pandas", "analytics", "reporting"]):
            if any(word in role_name or word in primary_cluster for word in ["data", "analytics", "analysis"]):
                boost += 18

        # AI/ML roles
        if any(word in combined for word in ["machine learning", "ml", "ai", "llm", "rag", "scikit", "model"]):
            if any(word in role_name or word in primary_cluster for word in ["ai", "ml", "machine learning"]):
                boost += 18

        # Business analyst roles
        if any(word in combined for word in ["business analyst", "requirement", "user story", "brd", "frd"]):
            if any(word in role_name or word in primary_cluster for word in ["business analysis", "business analyst"]):
                boost += 18

        # Database roles
        if any(word in combined for word in ["sql developer", "plsql", "oracle", "stored procedure", "database"]):
            if any(word in role_name or word in primary_cluster for word in ["database", "data engineering"]):
                boost += 14

        # Security roles
        if any(word in combined for word in ["security", "soc", "siem", "vulnerability", "incident response"]):
            if any(word in role_name or word in primary_cluster for word in ["security", "cyber"]):
                boost += 18

        return boost

    def _fallback_result(self, profile: CareerProfileRequest) -> RoleIntelligenceResult:
        return RoleIntelligenceResult(
            input_role=profile.current_role,
            canonical_role=profile.current_role,
            role_family="General IT",
            primary_cluster="General IT",
            secondary_clusters=[],

            matched_skills=profile.skills,
            missing_core_skills=[],
            missing_growth_skills=[],
            high_priority_missing_skills=[],

            adjacent_paths=[],
            confidence="Low",
            match_score=0.0,
        )

    def _calculate_confidence(self, score: float) -> str:
        if score >= 70:
            return "High"
        if score >= 40:
            return "Medium"
        return "Low"

    def _safe_list(self, value: Any) -> List[Any]:
        if value is None:
            return []

        if isinstance(value, list):
            return value

        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                return []

        return []

    def _normalize_text(self, text: str) -> str:
        text = text.lower().strip()
        text = text.replace("/", " ")
        text = text.replace("-", " ")
        text = text.replace("_", " ")
        text = text.replace("&", " and ")
        text = re.sub(r"[^a-z0-9+#. ]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _token_overlap_score(self, text1: str, text2: str) -> float:
        tokens1 = set(text1.split())
        tokens2 = set(text2.split())

        if not tokens1 or not tokens2:
            return 0.0

        intersection = tokens1.intersection(tokens2)
        union = tokens1.union(tokens2)

        return len(intersection) / len(union)

    def _dedupe_keep_order(self, values: List[str]) -> List[str]:
        seen = set()
        result = []

        for value in values:
            normalized = self._normalize_text(value)

            if normalized not in seen:
                seen.add(normalized)
                result.append(value)

        return result