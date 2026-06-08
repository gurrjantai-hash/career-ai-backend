import os
import json
import re
from typing import Any, Dict, List

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from app.models import CareerProfileRequest, RoleIntelligenceResult
from app.services.embedding_service import EmbeddingService

load_dotenv()


class RoleIntelligenceService:
    """
    Phase 2A + Phase 2C Role Intelligence Service.

    This service maps a user's free-text role + skills to a known canonical IT role.

    Phase 2A:
    - Uses canonical_roles + role_skill_matrix
    - Supports broad IT career families

    Phase 2C:
    - Adds embedding fallback for messy job titles
    - Embeddings are used only when existing matching is weak
    - High-confidence existing matches are not overridden
    """

    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")
        self.embedding_service = EmbeddingService()

    def map_role(self, profile: CareerProfileRequest) -> RoleIntelligenceResult:
        canonical_roles = self._load_canonical_roles()

        if not canonical_roles:
            fallback_result = self._fallback_result(profile)
            return self._apply_embedding_fallback_if_needed(profile, fallback_result)

        qa_consultant_result = self._resolve_qa_consultant_ambiguity(
            profile,
            canonical_roles
        )

        if qa_consultant_result:
            return qa_consultant_result

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
            fallback_result = self._fallback_result(profile)
            return self._apply_embedding_fallback_if_needed(profile, fallback_result)

        canonical_role_name = str(best_role.get("role_name", profile.current_role))

        role_skill_rows = self._load_role_skill_matrix(canonical_role_name)

        skill_gap_result = self._calculate_skill_gaps_from_matrix(
            profile=profile,
            role_skill_rows=role_skill_rows,
            fallback_matched_skills=best_matched_skills,
            fallback_missing_core_skills=best_missing_core_skills_from_canonical,
        )

        confidence = self._calculate_confidence(best_score)

        result = RoleIntelligenceResult(
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

        return self._apply_embedding_fallback_if_needed(profile, result)
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

    def _apply_embedding_fallback_if_needed(
        self,
        profile: CareerProfileRequest,
        result: RoleIntelligenceResult
    ) -> RoleIntelligenceResult:
        """
        Applies embedding-based matching only when existing match is weak.

        Safety rules:
        - Never override High confidence result.
        - Require embedding similarity >= 0.70.
        - Use skills/keywords as tie-breakers for ambiguous titles.
        """

        if not self._should_use_embedding_fallback(result):
            return result

        try:
            matches = self.embedding_service.find_closest_role(
                profile.current_role,
                limit=5
            )
        except Exception as e:
            print(f"Embedding fallback failed: {e}")
            return result

        if not matches:
            return result

        selected_match = self._select_embedding_match_with_guardrails(
            profile=profile,
            matches=matches
        )

        if not selected_match:
            return result

        similarity = float(selected_match.get("similarity", 0))

        if similarity < 0.70:
            return result

        canonical_role = selected_match.get("canonical_role")
        primary_cluster = selected_match.get("primary_cluster")
        role_family = selected_match.get("role_family")

        if not canonical_role or not primary_cluster:
            return result

        enriched_result = self._build_role_intelligence_from_embedding_match(
            profile=profile,
            canonical_role=str(canonical_role),
            primary_cluster=str(primary_cluster),
            role_family=str(role_family or "General IT"),
            similarity=similarity,
        )

        return enriched_result or result

    def _should_use_embedding_fallback(
        self,
        result: RoleIntelligenceResult
    ) -> bool:
        """
        Use embeddings only when existing role intelligence is weak.
        """

        if not result:
            return True

        confidence = (result.confidence or "").lower()
        primary_cluster = (result.primary_cluster or "").lower()

        if confidence == "high":
            return False

        if primary_cluster == "general it":
            return True

        if confidence in ["low", "medium"]:
            return True

        return False

    def _select_embedding_match_with_guardrails(
        self,
        profile: CareerProfileRequest,
        matches: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Selects best embedding match.

        Example:
        Associate Consultant QA may match Manual QA, API Tester, and Automation QA
        with equal similarity. Skills decide the final role.
        """

        role_text = self._normalize_text(profile.current_role)
        skills_text = self._normalize_text(" ".join(profile.skills or []))
        combined = f"{role_text} {skills_text}"

        def score_match(match: Dict[str, Any]) -> float:
            score = float(match.get("similarity", 0)) * 100

            primary_cluster = self._normalize_text(
                str(match.get("primary_cluster", ""))
            )

            # QA / Testing guardrails
            if any(word in combined for word in ["qa", "test", "testing", "tester"]):
                if primary_cluster in [
                    "testing qa",
                    "api testing",
                    "automation testing",
                    "performance testing",
                    "security testing",
                ]:
                    score += 25

                if primary_cluster in [
                    "salesforce",
                    "ai ml",
                    "backend engineering",
                    "business analysis",
                ]:
                    score -= 40

            if any(
                word in combined
                for word in ["postman", "api", "rest", "json", "status code", "status codes"]
            ):
                if primary_cluster == "api testing":
                    score += 35
                elif primary_cluster == "testing qa":
                    score += 10

            if any(
                word in combined
                for word in [
                    "selenium",
                    "restassured",
                    "rest assured",
                    "cypress",
                    "playwright",
                    "automation",
                    "testng",
                ]
            ):
                if primary_cluster == "automation testing":
                    score += 35

            if any(
                word in combined
                for word in ["manual", "test cases", "regression", "bug reporting", "jira"]
            ):
                if primary_cluster == "testing qa":
                    score += 30

            # Support / Operations guardrails
            if any(
                word in combined
                for word in ["support", "ops", "operations", "l2", "l3", "incident", "monitoring"]
            ):
                if primary_cluster in [
                    "application support",
                    "production support",
                    "cloud support",
                ]:
                    score += 25

            if any(
                word in combined
                for word in [
                    "app ops",
                    "application support",
                    "app support",
                    "application operations",
                ]
            ):
                if primary_cluster == "application support":
                    score += 35

            if any(word in combined for word in ["production", "prod"]):
                if primary_cluster == "production support":
                    score += 35

            if any(word in combined for word in ["cloud", "aws", "azure", "gcp"]):
                if primary_cluster in ["cloud support", "cloud engineering"]:
                    score += 35

            # Frontend guardrails
            if any(
                word in combined
                for word in ["ui", "frontend", "front end", "react", "angular", "javascript"]
            ):
                if primary_cluster == "frontend engineering":
                    score += 35
                if primary_cluster in ["business analysis", "cyber security"]:
                    score -= 35

            # BI / Data guardrails
            if any(
                word in combined
                for word in ["bi", "reporting", "dashboard", "power bi", "tableau"]
            ):
                if primary_cluster == "business intelligence":
                    score += 35
                if primary_cluster in ["application support", "security testing"]:
                    score -= 25

            # Business analyst guardrails
            if any(
                word in combined
                for word in ["requirement", "requirements", "user story", "brd", "frd"]
            ):
                if primary_cluster == "business analysis":
                    score += 35

            # Data/ML guardrails
            if any(
                word in combined
                for word in ["machine learning", "ml", "ai", "model", "pandas", "data science"]
            ):
                if primary_cluster in ["ai ml", "data science"]:
                    score += 35

            return score

        sorted_matches = sorted(matches, key=score_match, reverse=True)
        return sorted_matches[0]

    def _build_role_intelligence_from_embedding_match(
        self,
        profile: CareerProfileRequest,
        canonical_role: str,
        primary_cluster: str,
        role_family: str,
        similarity: float,
    ) -> RoleIntelligenceResult:
        """
        Builds RoleIntelligenceResult from embedding-selected canonical role.
        """

        canonical_role_row = self._load_canonical_role_by_name(canonical_role)

        if canonical_role_row:
            role_family = str(canonical_role_row.get("role_family", role_family))
            primary_cluster = str(
                canonical_role_row.get("primary_cluster", primary_cluster)
            )
            secondary_clusters = self._safe_list(
                canonical_role_row.get("secondary_clusters")
            )
            adjacent_paths = self._safe_list(canonical_role_row.get("adjacent_paths"))
            core_skills = self._safe_list(canonical_role_row.get("core_skills"))
        else:
            secondary_clusters = []
            adjacent_paths = []
            core_skills = []

        role_skill_rows = self._load_role_skill_matrix(canonical_role)

        fallback_matched_skills: List[str] = []
        fallback_missing_core_skills: List[str] = []

        user_skills_normalized = [
            self._normalize_text(skill)
            for skill in profile.skills
        ]

        for skill in core_skills:
            normalized_skill = self._normalize_text(str(skill))
            if self._is_skill_matched(normalized_skill, user_skills_normalized):
                fallback_matched_skills.append(str(skill))
            else:
                fallback_missing_core_skills.append(str(skill))

        skill_gap_result = self._calculate_skill_gaps_from_matrix(
            profile=profile,
            role_skill_rows=role_skill_rows,
            fallback_matched_skills=fallback_matched_skills,
            fallback_missing_core_skills=fallback_missing_core_skills,
        )

        confidence = "High" if similarity >= 0.85 else "Medium"

        return RoleIntelligenceResult(
            input_role=profile.current_role,
            canonical_role=canonical_role,
            role_family=role_family or "General IT",
            primary_cluster=primary_cluster or "General IT",
            secondary_clusters=secondary_clusters,

            matched_skills=skill_gap_result["matched_skills"],
            missing_core_skills=skill_gap_result["missing_core_skills"],
            missing_growth_skills=skill_gap_result["missing_growth_skills"],
            high_priority_missing_skills=skill_gap_result[
                "high_priority_missing_skills"
            ],

            adjacent_paths=adjacent_paths,
            confidence=confidence,
            match_score=round(similarity * 100, 2),
        )

    def _load_canonical_role_by_name(
        self,
        role_name: str
    ) -> Dict[str, Any]:
        if not self.database_url:
            return {}

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
                where lower(role_name) = lower(%s)
                limit 1
                """,
                (role_name,)
            )

            row = cursor.fetchone()

            cursor.close()
            connection.close()

            return dict(row) if row else {}

        except Exception as e:
            print(f"Failed to load canonical role by name {role_name}: {e}")
            return {}

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

    def _build_role_intelligence_from_role_row(
        self,
        profile: CareerProfileRequest,
        role: Dict[str, Any],
        confidence: str,
        match_score: float
    ) -> RoleIntelligenceResult:
        """
        Builds RoleIntelligenceResult from a canonical_roles row.
        Used for targeted overrides like ambiguous QA consultant titles.
        """

        canonical_role_name = str(role.get("role_name", profile.current_role))

        score_result = self._score_role_match(profile, role)

        role_skill_rows = self._load_role_skill_matrix(canonical_role_name)

        skill_gap_result = self._calculate_skill_gaps_from_matrix(
            profile=profile,
            role_skill_rows=role_skill_rows,
            fallback_matched_skills=score_result["matched_skills"],
            fallback_missing_core_skills=score_result["missing_core_skills"],
        )

        return RoleIntelligenceResult(
            input_role=profile.current_role,
            canonical_role=canonical_role_name,
            role_family=str(role.get("role_family", "General IT")),
            primary_cluster=str(role.get("primary_cluster", "General IT")),
            secondary_clusters=self._safe_list(role.get("secondary_clusters")),

            matched_skills=skill_gap_result["matched_skills"],
            missing_core_skills=skill_gap_result["missing_core_skills"],
            missing_growth_skills=skill_gap_result["missing_growth_skills"],
            high_priority_missing_skills=skill_gap_result[
                "high_priority_missing_skills"
            ],

            adjacent_paths=self._safe_list(role.get("adjacent_paths")),
            confidence=confidence,
            match_score=match_score,
        )
    
    def _resolve_qa_consultant_ambiguity(
        self,
        profile: CareerProfileRequest,
        canonical_roles: List[Dict[str, Any]]
    ) -> RoleIntelligenceResult | None:
        """
        Resolves ambiguous QA consultant titles.

        Example:
        "Associate Consultant QA" can mean:
        - Manual QA Engineer
        - API Tester
        - Automation QA Engineer

        The title alone is ambiguous, so skills should decide.
        """

        role_text = self._normalize_text(profile.current_role)
        skills_text = self._normalize_text(" ".join(profile.skills or []))

        is_qa_consultant_title = (
            "qa" in role_text
            and (
                "consultant" in role_text
                or "associate" in role_text
            )
        )

        if not is_qa_consultant_title:
            return None

        api_signal = any(
            word in skills_text
            for word in [
                "api",
                "api testing",
                "postman",
                "rest",
                "rest api",
                "json",
                "status code",
                "status codes",
                "swagger"
            ]
        )

        automation_signal = any(
            word in skills_text
            for word in [
                "selenium",
                "testng",
                "automation",
                "automation testing",
                "cypress",
                "playwright",
                "restassured",
                "java",
                "python",
                "automation framework"
            ]
        )

        manual_signal = any(
            word in skills_text
            for word in [
                "test cases",
                "test case",
                "regression",
                "regression testing",
                "bug reporting",
                "manual testing",
                "manual qa",
                "jira",
                "stlc",
                "test scenarios"
            ]
        )

        target_cluster = None

        if automation_signal:
            target_cluster = "Automation Testing"
        elif api_signal:
            target_cluster = "API Testing"
        elif manual_signal:
            target_cluster = "Testing/QA"

        if not target_cluster:
            return None

        selected_role = None

        for role in canonical_roles:
            primary_cluster = str(role.get("primary_cluster", ""))

            if self._normalize_text(primary_cluster) == self._normalize_text(target_cluster):
                selected_role = role
                break

        if not selected_role:
            return None

        return self._build_role_intelligence_from_role_row(
            profile=profile,
            role=selected_role,
            confidence="High",
            match_score=98.0
        )

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
        role_family = self._normalize_text(str(role.get("role_family", "")))
        combined = " ".join([input_role] + input_skills)

        boost = 0.0

        # ------------------------------------------------------------
        # QA / Testing disambiguation
        # ------------------------------------------------------------
        # Important because titles like "Associate Consultant QA" can match:
        # Manual QA, API Tester, and Automation QA at the same time.
        # Skills should decide the best cluster.

        api_signal = any(
            word in combined
            for word in [
                "api",
                "api testing",
                "postman",
                "rest",
                "rest api",
                "json",
                "status code",
                "status codes",
                "swagger"
            ]
        )

        automation_signal = any(
            word in combined
            for word in [
                "selenium",
                "testng",
                "automation",
                "automation testing",
                "cypress",
                "playwright",
                "restassured",
                "java",
                "python",
                "automation framework"
            ]
        )

        manual_qa_signal = any(
            word in combined
            for word in [
                "test cases",
                "test case",
                "regression",
                "regression testing",
                "bug reporting",
                "manual testing",
                "manual qa",
                "jira",
                "stlc",
                "test scenarios"
            ]
        )

        is_api_role = "api testing" in primary_cluster or "api tester" in role_name
        is_automation_role = (
            "automation testing" in primary_cluster
            or "automation" in role_name
            or "sdet" in primary_cluster
            or "sdet" in role_name
        )
        is_manual_qa_role = (
            "testing qa" in primary_cluster
            or "manual" in role_name
            or "qa" in role_name
        )

        if any(word in combined for word in ["qa", "tester", "testing", "test"]):
            if "quality assurance" in role_family or "testing" in primary_cluster or "qa" in role_name:
                boost += 15

        if api_signal:
            if is_api_role:
                boost += 45
            elif is_manual_qa_role:
                boost -= 15
            elif "salesforce" in primary_cluster:
                boost -= 50

        if automation_signal:
            if is_automation_role:
                boost += 45
            elif is_manual_qa_role:
                boost -= 20
            elif is_api_role:
                boost += 5

        if manual_qa_signal:
            if is_manual_qa_role:
                boost += 40
            elif is_api_role or is_automation_role:
                boost -= 10

        # ------------------------------------------------------------
        # Support roles
        # ------------------------------------------------------------
        if any(word in combined for word in ["support", "l2", "l3", "incident", "production", "app ops", "application support"]):
            if any(word in role_name or word in primary_cluster for word in ["support", "production", "application support"]):
                boost += 22

        # ------------------------------------------------------------
        # DevOps / Cloud roles
        # ------------------------------------------------------------
        if any(word in combined for word in ["devops", "docker", "kubernetes", "terraform", "ci cd", "cicd"]):
            if any(word in role_name or word in primary_cluster for word in ["devops", "cloud", "sre", "platform"]):
                boost += 18

        if any(word in combined for word in ["cloud", "aws", "azure", "gcp", "iam"]):
            if any(word in role_name or word in primary_cluster for word in ["cloud", "devops", "sre"]):
                boost += 18

        # ------------------------------------------------------------
        # Data / BI roles
        # ------------------------------------------------------------
        if any(word in combined for word in ["data analyst", "power bi", "tableau", "pandas", "analytics", "reporting", "dashboard"]):
            if any(word in role_name or word in primary_cluster for word in ["data", "analytics", "analysis", "business intelligence"]):
                boost += 18

        # ------------------------------------------------------------
        # AI/ML roles
        # ------------------------------------------------------------
        if any(word in combined for word in ["machine learning", "ml", "ai", "llm", "rag", "scikit", "model"]):
            if any(word in role_name or word in primary_cluster for word in ["ai", "ml", "machine learning"]):
                boost += 18

        # ------------------------------------------------------------
        # Business analyst roles
        # ------------------------------------------------------------
        if any(word in combined for word in ["business analyst", "requirement", "requirements", "user story", "user stories", "brd", "frd"]):
            if any(word in role_name or word in primary_cluster for word in ["business analysis", "business analyst"]):
                boost += 18

        # ------------------------------------------------------------
        # Product roles
        # ------------------------------------------------------------
        if any(word in combined for word in ["product owner", "backlog", "product roadmap", "roadmap", "product metrics", "funnel", "a b testing"]):
            if any(word in role_name or word in primary_cluster for word in ["product", "product analysis"]):
                boost += 30
            elif any(word in role_name or word in primary_cluster for word in ["business analyst", "business analysis"]):
                boost += 8

        # ------------------------------------------------------------
        # Agile / delivery roles
        # ------------------------------------------------------------
        if any(word in combined for word in ["agile delivery", "scrum", "sprint", "agile metrics", "team facilitation", "stakeholder management"]):
            if any(word in role_name or word in primary_cluster for word in ["scrum", "agile", "agile delivery"]):
                boost += 30

        # ------------------------------------------------------------
        # Database roles
        # ------------------------------------------------------------
        if any(word in combined for word in ["sql developer", "plsql", "oracle", "stored procedure", "database"]):
            if any(word in role_name or word in primary_cluster for word in ["database", "data engineering"]):
                boost += 14

        # ------------------------------------------------------------
        # Security roles
        # ------------------------------------------------------------
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
        if not text:
            return ""

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
