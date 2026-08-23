import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from app.models import CareerProfileRequest


load_dotenv()


@dataclass
class RolePathRule:
    role_cluster: str
    role_family: str
    signal_keywords: List[str] = field(default_factory=list)
    target_role_keywords: List[str] = field(default_factory=list)
    target_roles_by_experience: Dict[str, List[str]] = field(default_factory=dict)
    stretch_roles_by_experience: Dict[str, List[str]] = field(default_factory=dict)
    skill_gaps_by_experience: Dict[str, List[str]] = field(default_factory=dict)
    blocked_role_keywords: Dict[str, List[str]] = field(default_factory=dict)
    evidence_keywords: Dict[str, List[str]] = field(default_factory=dict)
    notes: str = ""


@dataclass
class RoleGuardrailDecision:
    original_role_cluster: str
    final_role_cluster: str
    experience_band: str
    allowed_target_roles: List[str]
    stretch_target_roles: List[str]
    priority_skill_gaps: List[str]
    rule_notes: str
    prompt_context: str
    rule_source: str = "fallback"


class RoleGuardrailService:
    """
    Role Guardrails v2.

    The purpose of this service is to stop the AI from freely deciding career
    direction. The product engine decides the allowed career family and target
    role set from role, skills, experience and configured rules. AI only
    explains and personalizes inside those boundaries.

    Rules are loaded from public.career_path_rules when available. If the table
    has not been created yet, the service falls back to the built-in rule set so
    local development does not break.
    """

    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")
        self._rules_cache: Optional[List[RolePathRule]] = None
        self._rule_source = "fallback"

    def build_decision(
        self,
        profile: CareerProfileRequest,
        suggested_role_cluster: str,
    ) -> RoleGuardrailDecision:
        rules = self._load_rules()
        original_cluster = self._safe_text(suggested_role_cluster, "General IT")
        final_cluster = self.resolve_role_cluster(profile, original_cluster, rules)
        rule = self._find_rule(final_cluster, rules) or self._fallback_general_rule()
        band = self._experience_band(profile.experience_years)

        allowed_roles = self._band_values(
            rule.target_roles_by_experience,
            band,
        )
        stretch_roles = self._band_values(
            rule.stretch_roles_by_experience,
            band,
        )
        skill_gaps = self._select_skill_gaps(
            profile=profile,
            rule=rule,
            band=band,
        )

        prompt_context = self._build_prompt_context(
            profile=profile,
            rule=rule,
            original_cluster=original_cluster,
            final_cluster=final_cluster,
            band=band,
            allowed_roles=allowed_roles,
            stretch_roles=stretch_roles,
            skill_gaps=skill_gaps,
        )

        return RoleGuardrailDecision(
            original_role_cluster=original_cluster,
            final_role_cluster=final_cluster,
            experience_band=band,
            allowed_target_roles=allowed_roles,
            stretch_target_roles=stretch_roles,
            priority_skill_gaps=skill_gaps,
            rule_notes=rule.notes,
            prompt_context=prompt_context,
            rule_source=self._rule_source,
        )

    def resolve_role_cluster(
        self,
        profile: CareerProfileRequest,
        suggested_role_cluster: str,
        rules: Optional[List[RolePathRule]] = None,
    ) -> str:
        rules = rules or self._load_rules()
        profile_text = self._profile_text(profile)
        suggested = self._safe_text(suggested_role_cluster, "General IT")
        suggested_rule = self._find_rule(suggested, rules)
        suggested_score = self._score_rule(profile_text, suggested_rule) if suggested_rule else 0

        best_rule = None
        best_score = 0
        for rule in rules:
            score = self._score_rule(profile_text, rule)
            if score > best_score:
                best_score = score
                best_rule = rule

        # Strong evidence from user's actual role/skills wins over weak role mapping.
        if best_rule and best_score >= 2 and best_score >= suggested_score:
            return best_rule.role_cluster

        if suggested_rule:
            return suggested_rule.role_cluster

        return suggested or "General IT"

    def validate_ai_result(
        self,
        ai_result: Dict[str, Any],
        profile: CareerProfileRequest,
        decision: RoleGuardrailDecision,
    ) -> Dict[str, Any]:
        rules = self._load_rules()
        rule = self._find_rule(decision.final_role_cluster, rules) or self._fallback_general_rule()

        target_roles, rejected_roles = self._guard_target_roles(
            ai_roles=self._normalize_list(ai_result.get("target_roles")),
            profile=profile,
            rule=rule,
            decision=decision,
        )

        top_skill_gaps = self._guard_skill_gaps(
            ai_skill_gaps=self._normalize_list(ai_result.get("top_skill_gaps")),
            profile=profile,
            rule=rule,
            decision=decision,
            rejected_roles=rejected_roles,
        )

        growth_paths = self._guard_growth_paths(
            ai_growth_paths=ai_result.get("growth_paths"),
            target_roles=target_roles,
            top_skill_gaps=top_skill_gaps,
            decision=decision,
            rejected_roles=rejected_roles,
            rule=rule,
        )

        skill_salary_impact = self._guard_skill_salary_impact(
            ai_skill_salary_impact=ai_result.get("skill_salary_impact"),
            top_skill_gaps=top_skill_gaps,
        )

        roadmap = self._guard_roadmap(
            ai_roadmap=ai_result.get("roadmap_4_weeks"),
            target_roles=target_roles,
            top_skill_gaps=top_skill_gaps,
        )

        resume_suggestions = self._guard_resume_suggestions(
            ai_suggestions=self._normalize_list(ai_result.get("resume_suggestions")),
            target_roles=target_roles,
            top_skill_gaps=top_skill_gaps,
        )

        summary_copy = self._maybe_override_direction_copy(
            ai_result=ai_result,
            profile=profile,
            target_roles=target_roles,
            top_skill_gaps=top_skill_gaps,
            decision=decision,
            rejected_roles=rejected_roles,
            rule=rule,
        )

        confidence_notes = self._guard_confidence_notes(
            ai_notes=self._normalize_list(ai_result.get("confidence_notes")),
            decision=decision,
            rejected_roles=rejected_roles,
        )

        return {
            "target_roles": target_roles,
            "rejected_roles": rejected_roles,
            "top_skill_gaps": top_skill_gaps,
            "growth_paths": growth_paths,
            "skill_salary_impact": skill_salary_impact,
            "roadmap_4_weeks": roadmap,
            "resume_suggestions": resume_suggestions,
            "summary": summary_copy["summary"],
            "recommended_next_move": summary_copy["recommended_next_move"],
            "goal_strategy": summary_copy["goal_strategy"],
            "why_recommendations": summary_copy["why_recommendations"],
            "confidence_notes": confidence_notes,
        }

    def current_level_from_experience(self, experience_years: Any) -> str:
        experience = self._safe_float(experience_years)
        if experience >= 8:
            return "Lead"
        if experience >= 5:
            return "Senior"
        if experience >= 2:
            return "Mid"
        return "Junior"

    def _guard_target_roles(
        self,
        ai_roles: List[str],
        profile: CareerProfileRequest,
        rule: RolePathRule,
        decision: RoleGuardrailDecision,
    ) -> Tuple[List[str], List[str]]:
        allowed = decision.allowed_target_roles
        stretch = decision.stretch_target_roles
        valid_roles: List[str] = []
        rejected_roles: List[str] = []

        for role in ai_roles:
            if self._is_role_allowed(
                role=role,
                profile=profile,
                rule=rule,
                allowed_roles=allowed,
                stretch_roles=stretch,
            ):
                valid_roles.append(self._canonicalize_role(role, allowed + stretch))
            else:
                rejected_roles.append(role)

        # Product decision wins. AI may reorder or explain, but final options should
        # be mostly the configured career path roles.
        final_roles = self._dedupe_keep_order(valid_roles + allowed)
        if len(final_roles) < 3:
            final_roles = self._dedupe_keep_order(final_roles + stretch)

        return final_roles[:4], self._dedupe_keep_order(rejected_roles)

    def _is_role_allowed(
        self,
        role: str,
        profile: CareerProfileRequest,
        rule: RolePathRule,
        allowed_roles: List[str],
        stretch_roles: List[str],
    ) -> bool:
        role_text = self._normalize_text(role)
        profile_text = self._profile_text(profile)

        for group_name, blocked_terms in rule.blocked_role_keywords.items():
            if self._has_any(role_text, blocked_terms):
                evidence = rule.evidence_keywords.get(group_name, [])
                if not self._has_any(profile_text, evidence):
                    return False

        if self._matches_any_role(role_text, allowed_roles + stretch_roles):
            return True

        if rule.target_role_keywords and self._has_any(role_text, rule.target_role_keywords):
            return True

        return False

    def _guard_skill_gaps(
        self,
        ai_skill_gaps: List[str],
        profile: CareerProfileRequest,
        rule: RolePathRule,
        decision: RoleGuardrailDecision,
        rejected_roles: List[str],
    ) -> List[str]:
        blocked_terms = []
        for terms in rule.blocked_role_keywords.values():
            blocked_terms.extend(terms)

        valid_ai_gaps = []
        for gap in ai_skill_gaps:
            gap_text = self._normalize_text(gap)
            if self._has_any(gap_text, blocked_terms):
                continue
            if self._is_skill_already_present(gap, profile):
                continue
            valid_ai_gaps.append(gap)

        if rejected_roles or len(valid_ai_gaps) < 2:
            return decision.priority_skill_gaps[:3]

        return self._dedupe_keep_order(valid_ai_gaps + decision.priority_skill_gaps)[:3]

    def _guard_growth_paths(
        self,
        ai_growth_paths: Any,
        target_roles: List[str],
        top_skill_gaps: List[str],
        decision: RoleGuardrailDecision,
        rejected_roles: List[str],
        rule: RolePathRule,
    ) -> List[Dict[str, Any]]:
        if rejected_roles or not isinstance(ai_growth_paths, list):
            return self._build_default_growth_paths(
                target_roles=target_roles,
                top_skill_gaps=top_skill_gaps,
                decision=decision,
            )

        guarded_paths = []
        allowed_role_text = " ".join(target_roles + decision.stretch_target_roles)

        for path in ai_growth_paths:
            if not isinstance(path, dict):
                continue

            path_text = self._normalize_text(path)
            path_invalid = False
            for group_name, blocked_terms in rule.blocked_role_keywords.items():
                if self._has_any(path_text, blocked_terms):
                    path_invalid = True
                    break

            if path_invalid:
                continue

            path_roles = self._normalize_list(path.get("target_roles"))
            safe_path_roles = [
                role for role in path_roles
                if self._has_any(self._normalize_text(role), target_roles)
                or self._has_any(self._normalize_text(role), [allowed_role_text])
            ]

            guarded_paths.append(
                {
                    "path_name": self._safe_text(path.get("path_name"), "Career Growth Path"),
                    "fit_score": self._safe_text(path.get("fit_score"), "Medium"),
                    "why_it_fits": self._safe_text(
                        path.get("why_it_fits"),
                        "This path fits based on the user's current role, skills, experience and career goal.",
                    ),
                    "target_roles": (safe_path_roles or target_roles[:3])[:3],
                    "skills_to_build": self._dedupe_keep_order(
                        self._normalize_list(path.get("skills_to_build")) + top_skill_gaps
                    )[:3],
                }
            )

        return guarded_paths[:3] or self._build_default_growth_paths(
            target_roles=target_roles,
            top_skill_gaps=top_skill_gaps,
            decision=decision,
        )

    def _build_default_growth_paths(
        self,
        target_roles: List[str],
        top_skill_gaps: List[str],
        decision: RoleGuardrailDecision,
    ) -> List[Dict[str, Any]]:
        primary_role = target_roles[0] if target_roles else "the best-fit target role"
        secondary_role = target_roles[1] if len(target_roles) > 1 else primary_role
        stretch_roles = decision.stretch_target_roles or target_roles[2:]
        stretch_role = stretch_roles[0] if stretch_roles else secondary_role

        return [
            {
                "path_name": f"{decision.final_role_cluster} Core Growth Track",
                "fit_score": "High",
                "why_it_fits": (
                    f"This path keeps the user in the strongest current career family: "
                    f"{decision.final_role_cluster}. It uses existing experience while improving salary and switch readiness."
                ),
                "target_roles": target_roles[:3],
                "skills_to_build": top_skill_gaps[:3],
            },
            {
                "path_name": "Leadership / Seniority Upgrade Track",
                "fit_score": "Medium",
                "why_it_fits": (
                    "This path can improve compensation and seniority, but it needs proof of ownership, design decisions, mentoring, impact and interview readiness."
                ),
                "target_roles": self._dedupe_keep_order([secondary_role, stretch_role])[:3],
                "skills_to_build": top_skill_gaps[:3],
            },
            {
                "path_name": "Adjacent Skill Expansion Track",
                "fit_score": "Medium",
                "why_it_fits": (
                    "This path adds adjacent high-value skills without forcing an unrelated career switch. It should be used to strengthen the primary role, not replace it blindly."
                ),
                "target_roles": self._dedupe_keep_order([primary_role, stretch_role])[:3],
                "skills_to_build": top_skill_gaps[:3],
            },
        ]

    def _guard_skill_salary_impact(
        self,
        ai_skill_salary_impact: Any,
        top_skill_gaps: List[str],
    ) -> Dict[str, str]:
        guarded: Dict[str, str] = {}

        if isinstance(ai_skill_salary_impact, dict):
            for skill in top_skill_gaps:
                existing = ai_skill_salary_impact.get(skill)
                if isinstance(existing, str) and existing.strip():
                    guarded[skill] = existing.strip()

        for skill in top_skill_gaps:
            if skill not in guarded:
                guarded[skill] = (
                    f"{skill} improves salary and job-switch positioning because it shows depth beyond the current baseline and creates stronger proof for target roles."
                )

        return guarded

    def _guard_roadmap(
        self,
        ai_roadmap: Any,
        target_roles: List[str],
        top_skill_gaps: List[str],
    ) -> Dict[str, List[str]]:
        primary_role = target_roles[0] if target_roles else "target role"
        skill_focus = self._format_phrase(top_skill_gaps[:2], "priority skills")

        # Keep Career Report roadmap lightweight because Learning Plan and
        # Execution Plan own detailed weekly tasks.
        return {
            "week_1": [
                f"Validate {primary_role} as the primary target and compare it with current skills."
            ],
            "week_2": [
                f"Prioritize {skill_focus} as the highest-impact gap area."
            ],
            "week_3": [
                "Collect proof points from past work that show ownership, impact and role readiness."
            ],
            "week_4": [
                f"Use Resume Optimizer and Execution Plan to convert this direction into tracked action for {primary_role}."
            ],
        }

    def _guard_resume_suggestions(
        self,
        ai_suggestions: List[str],
        target_roles: List[str],
        top_skill_gaps: List[str],
    ) -> List[str]:
        primary_role = target_roles[0] if target_roles else "target role"
        suggestions = self._dedupe_keep_order(ai_suggestions)

        defaults = [
            f"Rewrite the resume summary for {primary_role} with current role, strongest stack, ownership and measurable impact.",
            "Add metrics wherever true: scale handled, latency improvement, defect reduction, automation saved, release ownership, revenue/cost impact, or production stability.",
            f"Add proof around {self._format_phrase(top_skill_gaps[:2], 'priority skills')} through project, production work, architecture notes or interview stories.",
        ]

        return self._dedupe_keep_order(suggestions + defaults)[:5]

    def _maybe_override_direction_copy(
        self,
        ai_result: Dict[str, Any],
        profile: CareerProfileRequest,
        target_roles: List[str],
        top_skill_gaps: List[str],
        decision: RoleGuardrailDecision,
        rejected_roles: List[str],
        rule: RolePathRule,
    ) -> Dict[str, Any]:
        summary = self._safe_text(
            ai_result.get("summary"),
            "Career summary is not available for this analysis.",
        )
        recommended_next_move = self._safe_text(
            ai_result.get("recommended_next_move"),
            "Recommended next move is not available for this analysis.",
        )
        goal_strategy = self._safe_text(
            ai_result.get("goal_strategy"),
            f"The selected goal is {profile.goal}. The strategy should align with this goal.",
        )
        why_recommendations = self._normalize_list(ai_result.get("why_recommendations"))

        combined = self._normalize_text(f"{summary} {recommended_next_move} {goal_strategy}")
        invalid_direction = False
        for terms in rule.blocked_role_keywords.values():
            if self._has_any(combined, terms):
                invalid_direction = True
                break

        if not rejected_roles and not invalid_direction:
            return {
                "summary": summary,
                "recommended_next_move": recommended_next_move,
                "goal_strategy": goal_strategy,
                "why_recommendations": why_recommendations,
            }

        primary_role = target_roles[0] if target_roles else "the best-fit target role"
        secondary_role = target_roles[1] if len(target_roles) > 1 else primary_role
        skill_focus = self._format_phrase(top_skill_gaps[:3], "the highest-priority skill gaps")

        guarded_summary = (
            f"With {profile.experience_years} years of experience as {profile.current_role}, "
            f"the strongest direction is {decision.final_role_cluster}. For the goal of {profile.goal}, "
            f"the most realistic path is to target {primary_role} or {secondary_role} and improve {skill_focus}."
        )
        guarded_next_move = (
            f"Focus first on {primary_role} positioning: close {skill_focus}, strengthen proof from existing work, and prepare role-specific resume and interview stories."
        )
        guarded_strategy = (
            f"Because the selected goal is {profile.goal}, the strategy should preserve the user's strongest existing career capital instead of forcing a weak-fit direction. "
            f"Adjacent or stretch paths should be considered only after evidence is built."
        )
        guarded_why = [
            f"The current role and skills match {decision.final_role_cluster} more strongly than unrelated career families.",
            f"Target roles were selected from configured product guardrails for the user's experience band: {decision.experience_band}.",
            f"The recommended skill gaps focus on {skill_focus}, which can improve target-role readiness and salary positioning.",
        ]

        if rejected_roles:
            guarded_why.append(
                f"Removed weak-fit AI suggestions because the profile did not contain enough evidence: {', '.join(rejected_roles)}."
            )

        return {
            "summary": guarded_summary,
            "recommended_next_move": guarded_next_move,
            "goal_strategy": guarded_strategy,
            "why_recommendations": guarded_why,
        }

    def _guard_confidence_notes(
        self,
        ai_notes: List[str],
        decision: RoleGuardrailDecision,
        rejected_roles: List[str],
    ) -> List[str]:
        notes = [
            (
                f"Product guardrail: final role cluster is '{decision.final_role_cluster}' "
                f"for experience band '{decision.experience_band}' using {decision.rule_source} career path rules."
            )
        ]

        if decision.original_role_cluster != decision.final_role_cluster:
            notes.append(
                f"Role cluster was corrected from '{decision.original_role_cluster}' to '{decision.final_role_cluster}' because role/skill evidence was stronger."
            )

        if rejected_roles:
            notes.append(
                f"AI-suggested roles removed as weak-fit: {', '.join(rejected_roles)}."
            )

        return self._dedupe_keep_order(notes + ai_notes)

    def _load_rules(self) -> List[RolePathRule]:
        if self._rules_cache is not None:
            return self._rules_cache

        db_rules = self._load_rules_from_db()
        if db_rules:
            self._rules_cache = db_rules
            self._rule_source = "database"
            return db_rules

        self._rules_cache = self._default_rules()
        self._rule_source = "fallback"
        return self._rules_cache

    def _load_rules_from_db(self) -> List[RolePathRule]:
        if not self.database_url:
            return []

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                """
                select
                    role_cluster,
                    role_family,
                    signal_keywords,
                    target_role_keywords,
                    target_roles_by_experience,
                    stretch_roles_by_experience,
                    skill_gaps_by_experience,
                    blocked_role_keywords,
                    evidence_keywords,
                    notes
                from career_path_rules
                where is_active = true
                order by role_cluster
                """
            )
            rows = cursor.fetchall()
            cursor.close()
            connection.close()

            return [self._row_to_rule(row) for row in rows]

        except Exception as exc:
            print(f"Role guardrail rules DB load failed, using fallback rules: {exc}")
            return []

    def _row_to_rule(self, row: Dict[str, Any]) -> RolePathRule:
        return RolePathRule(
            role_cluster=self._safe_text(row.get("role_cluster"), "General IT"),
            role_family=self._safe_text(row.get("role_family"), "General IT"),
            signal_keywords=self._json_list(row.get("signal_keywords")),
            target_role_keywords=self._json_list(row.get("target_role_keywords")),
            target_roles_by_experience=self._json_dict(row.get("target_roles_by_experience")),
            stretch_roles_by_experience=self._json_dict(row.get("stretch_roles_by_experience")),
            skill_gaps_by_experience=self._json_dict(row.get("skill_gaps_by_experience")),
            blocked_role_keywords=self._json_dict(row.get("blocked_role_keywords")),
            evidence_keywords=self._json_dict(row.get("evidence_keywords")),
            notes=self._safe_text(row.get("notes"), ""),
        )

    def _default_rules(self) -> List[RolePathRule]:
        return [
            RolePathRule(
                role_cluster="Backend Engineering",
                role_family="Software Engineering",
                signal_keywords=[
                    "backend", "developer", "software engineer", "java", "spring boot", "springboot",
                    "microservice", "microservices", "hibernate", "jpa", "rest api", "api development",
                    "kafka", "redis", "sql", "mysql", "postgres",
                ],
                target_role_keywords=["backend", "java", "microservice", "technical lead", "tech lead", "architect", "platform", "principal", "staff"],
                target_roles_by_experience={
                    "entry": ["Associate Backend Engineer", "Java Developer", "Backend Developer"],
                    "mid": ["Backend Engineer", "Java Backend Developer", "Microservices Developer", "Software Engineer II"],
                    "senior": ["Senior Backend Engineer", "Lead Backend Engineer", "Java Technical Lead", "Microservices Developer"],
                    "lead": ["Lead Backend Engineer", "Java Technical Lead", "Senior Backend Engineer", "Backend Architect"],
                },
                stretch_roles_by_experience={
                    "entry": ["Backend Engineer"],
                    "mid": ["Senior Backend Engineer", "Cloud Backend Engineer"],
                    "senior": ["Backend Architect", "Platform Engineer"],
                    "lead": ["Principal Backend Engineer", "Staff Backend Engineer", "Platform Engineer"],
                },
                skill_gaps_by_experience={
                    "entry": ["Java Depth", "Spring Boot APIs", "SQL and Debugging"],
                    "mid": ["System Design Basics", "Kafka / Event-driven Architecture", "AWS / Cloud Deployment", "Caching and Performance Optimization"],
                    "senior": ["System Design", "Kafka / Event-driven Architecture", "Docker and Kubernetes Basics", "Spring Security / OAuth2", "Database Optimization"],
                    "lead": ["System Design", "Architecture Decision Making", "Kafka / Event-driven Architecture", "Cloud Deployment", "Technical Leadership"],
                },
                blocked_role_keywords={
                    "testing": ["sdet", "qa", "tester", "testing", "automation qa", "test engineer", "test architect", "selenium", "restassured"],
                    "devops": ["devops", "sre", "site reliability", "infrastructure engineer"],
                    "data": ["data scientist", "ml engineer", "machine learning"],
                },
                evidence_keywords={
                    "testing": ["qa", "testing", "selenium", "restassured", "testng", "cypress", "playwright", "automation testing", "manual testing"],
                    "devops": ["aws", "azure", "gcp", "cloud", "docker", "kubernetes", "k8s", "terraform", "jenkins", "ci/cd", "cicd", "devops", "sre"],
                    "data": ["python", "pandas", "machine learning", "ml", "statistics", "data science"],
                },
                notes="Backend developer profiles should stay on backend, Java leadership, architecture or platform-adjacent paths unless strong evidence supports a different family.",
            ),
            RolePathRule(
                role_cluster="Frontend Engineering",
                role_family="Software Engineering",
                signal_keywords=["frontend", "front end", "react", "angular", "vue", "javascript", "typescript", "html", "css", "next.js", "ui developer"],
                target_role_keywords=["frontend", "front end", "react", "ui", "product engineer", "web"],
                target_roles_by_experience={
                    "entry": ["Associate Frontend Engineer", "UI Developer", "React Developer"],
                    "mid": ["Frontend Engineer", "React Developer", "Product Engineer", "Software Engineer II - Frontend"],
                    "senior": ["Senior Frontend Engineer", "Lead Frontend Engineer", "Product Engineer", "Frontend Specialist"],
                    "lead": ["Lead Frontend Engineer", "Frontend Architect", "Senior Product Engineer", "UI Platform Lead"],
                },
                stretch_roles_by_experience={
                    "mid": ["Senior Frontend Engineer"],
                    "senior": ["Frontend Architect"],
                    "lead": ["Principal Frontend Engineer", "Design Systems Lead"],
                },
                skill_gaps_by_experience={
                    "entry": ["JavaScript Depth", "React Fundamentals", "API Integration"],
                    "mid": ["TypeScript", "State Management", "Frontend Testing", "Performance Optimization"],
                    "senior": ["Frontend Architecture", "Design Systems", "Performance Optimization", "Testing Strategy"],
                    "lead": ["Frontend Architecture", "Design Systems Leadership", "Technical Mentoring", "Performance Strategy"],
                },
                blocked_role_keywords={"backend": ["backend", "java backend"], "testing": ["qa", "sdet", "tester"]},
                evidence_keywords={"backend": ["java", "spring", "backend", "api development"], "testing": ["qa", "testing", "selenium", "cypress", "playwright"]},
                notes="Frontend users should move toward frontend depth, product engineering, design systems or frontend architecture before unrelated tracks.",
            ),
            RolePathRule(
                role_cluster="Full Stack Engineering",
                role_family="Software Engineering",
                signal_keywords=["full stack", "fullstack", "mern", "mean", "react", "angular", "node", "java", "spring boot", "frontend", "backend"],
                target_role_keywords=["full stack", "fullstack", "product engineer", "backend", "frontend"],
                target_roles_by_experience={
                    "entry": ["Associate Full Stack Developer", "Frontend Developer", "Backend Developer"],
                    "mid": ["Full Stack Engineer", "Product Engineer", "Backend Engineer", "Frontend Engineer"],
                    "senior": ["Senior Full Stack Engineer", "Product Engineer", "Lead Full Stack Engineer", "Senior Backend Engineer"],
                    "lead": ["Lead Full Stack Engineer", "Java Technical Lead", "Product Engineering Lead", "Backend Architect"],
                },
                stretch_roles_by_experience={"senior": ["Solution Architect"], "lead": ["Solution Architect", "Engineering Manager"]},
                skill_gaps_by_experience={
                    "mid": ["System Design Basics", "TypeScript", "API Design", "Cloud Deployment"],
                    "senior": ["System Design", "Frontend Architecture", "Backend Architecture", "Cloud Deployment"],
                    "lead": ["Solution Design", "Architecture Decision Making", "Technical Leadership", "Delivery Ownership"],
                },
                blocked_role_keywords={"testing": ["qa", "sdet", "tester"]},
                evidence_keywords={"testing": ["qa", "testing", "selenium", "cypress", "playwright"]},
                notes="Full stack profiles should be positioned by strongest side: product/full stack, backend depth, frontend depth, or architecture.",
            ),
            RolePathRule(
                role_cluster="Testing/QA",
                role_family="Quality Engineering",
                signal_keywords=["qa", "quality assurance", "tester", "testing", "manual testing", "test cases", "bug", "regression", "jira"],
                target_role_keywords=["qa", "test", "testing", "sdet", "quality", "automation"],
                target_roles_by_experience={
                    "entry": ["Manual QA Engineer", "QA Tester", "Associate Test Engineer"],
                    "mid": ["QA Engineer", "API Tester", "Automation QA Engineer", "Test Analyst"],
                    "senior": ["Senior QA Engineer", "QA Lead", "Senior Automation QA Engineer", "API Test Lead"],
                    "lead": ["QA Lead", "Test Manager", "Test Architect", "Quality Engineering Lead"],
                },
                stretch_roles_by_experience={"mid": ["SDET"], "senior": ["SDET", "Performance Test Engineer"], "lead": ["Quality Architect", "Engineering Manager - QA"]},
                skill_gaps_by_experience={
                    "entry": ["Test Case Design", "Bug Reporting", "API Testing Basics"],
                    "mid": ["API Testing", "SQL Basics", "Java/Python Foundation", "Selenium Basics"],
                    "senior": ["Automation Framework Design", "API Automation", "CI Test Execution", "Test Strategy"],
                    "lead": ["Test Strategy", "Automation Architecture", "Quality Metrics", "Team Leadership"],
                },
                blocked_role_keywords={"backend": ["backend developer", "backend architect", "java technical lead"], "devops": ["devops", "sre"]},
                evidence_keywords={"backend": ["java", "spring", "api development", "microservices"], "devops": ["docker", "kubernetes", "cloud", "ci/cd", "jenkins"]},
                notes="QA users should move through manual/API/automation/SDET/lead paths based on programming and automation evidence.",
            ),
            RolePathRule(
                role_cluster="API Testing",
                role_family="Quality Engineering",
                signal_keywords=["api testing", "postman", "json", "status code", "status codes", "rest assured", "restassured", "newman"],
                target_role_keywords=["api tester", "automation qa", "sdet", "quality"],
                target_roles_by_experience={
                    "entry": ["API Tester", "QA Engineer", "Manual QA Engineer"],
                    "mid": ["API Tester", "Automation QA Engineer", "Senior QA Engineer", "SDET"],
                    "senior": ["Senior API Tester", "API Automation Engineer", "SDET", "QA Lead"],
                    "lead": ["API Test Lead", "Test Architect", "Quality Engineering Lead", "SDET Lead"],
                },
                stretch_roles_by_experience={"mid": ["SDET"], "senior": ["SDET Lead"], "lead": ["Quality Architect"]},
                skill_gaps_by_experience={
                    "entry": ["HTTP/API Test Design", "Postman Collections", "SQL Validation"],
                    "mid": ["RestAssured", "SQL Validation", "Newman / CI Execution", "Java/Python Foundation"],
                    "senior": ["API Automation Framework", "CI Test Execution", "Performance Testing Basics", "Contract Testing"],
                    "lead": ["API Test Strategy", "Automation Architecture", "Quality Metrics", "Team Leadership"],
                },
                blocked_role_keywords={"backend": ["backend architect", "java technical lead"], "devops": ["devops", "sre"]},
                evidence_keywords={"backend": ["java", "spring", "microservices"], "devops": ["docker", "kubernetes", "jenkins", "cloud"]},
                notes="API testers should not be pushed into pure backend unless development evidence is strong.",
            ),
            RolePathRule(
                role_cluster="Application Support",
                role_family="Operations / Support",
                signal_keywords=["application support", "production support", "support engineer", "l2", "l3", "incident", "monitoring", "logs", "service now", "servicenow", "rca"],
                target_role_keywords=["support", "production", "cloud support", "operations", "observability", "sre", "devops"],
                target_roles_by_experience={
                    "entry": ["Application Support Engineer", "Production Support Engineer", "L1/L2 Support Engineer"],
                    "mid": ["Application Support Engineer", "Production Support Engineer", "Cloud Support Engineer", "Support Automation Engineer"],
                    "senior": ["Senior Application Support Engineer", "Production Support Lead", "Cloud Support Engineer", "Observability Engineer"],
                    "lead": ["Production Support Lead", "Application Support Lead", "Cloud Operations Lead", "Observability Lead"],
                },
                stretch_roles_by_experience={"mid": ["DevOps Engineer"], "senior": ["DevOps Engineer", "SRE Engineer"], "lead": ["SRE Lead", "DevOps Lead"]},
                skill_gaps_by_experience={
                    "entry": ["Linux Commands", "SQL Debugging", "Log Analysis"],
                    "mid": ["Shell Scripting", "Cloud Fundamentals", "Docker Basics", "Monitoring and RCA"],
                    "senior": ["Observability", "Automation Scripting", "Cloud Operations", "Docker and CI/CD Basics"],
                    "lead": ["Incident Leadership", "Observability Strategy", "Automation Roadmap", "Cloud Operations"],
                },
                blocked_role_keywords={"backend": ["backend developer", "java developer", "backend architect"], "data": ["data scientist", "ml engineer"]},
                evidence_keywords={"backend": ["java", "spring", "api development"], "data": ["python", "pandas", "machine learning", "statistics"]},
                notes="Support profiles can bridge to cloud/DevOps/SRE only when scripting, cloud, Docker, CI/CD or infra evidence appears.",
            ),
            RolePathRule(
                role_cluster="Cloud Engineering",
                role_family="Cloud / Infrastructure",
                signal_keywords=["cloud", "aws", "azure", "gcp", "iam", "ec2", "s3", "vpc", "cloud support", "cloud engineer"],
                target_role_keywords=["cloud", "devops", "sre", "platform", "infrastructure"],
                target_roles_by_experience={
                    "entry": ["Cloud Support Associate", "Junior Cloud Engineer", "Cloud Operations Associate"],
                    "mid": ["Cloud Engineer", "Cloud Support Engineer", "DevOps Engineer", "Cloud Operations Engineer"],
                    "senior": ["Senior Cloud Engineer", "Cloud DevOps Engineer", "SRE Engineer", "Platform Engineer"],
                    "lead": ["Cloud Lead", "Cloud Architect", "Platform Engineering Lead", "SRE Lead"],
                },
                stretch_roles_by_experience={"mid": ["SRE Engineer"], "senior": ["Cloud Architect"], "lead": ["Principal Cloud Architect"]},
                skill_gaps_by_experience={
                    "entry": ["Cloud Fundamentals", "Linux", "Networking Basics", "IAM"],
                    "mid": ["Docker", "Terraform Basics", "CI/CD", "Monitoring"],
                    "senior": ["Kubernetes", "Terraform", "Observability", "Cloud Security Basics"],
                    "lead": ["Cloud Architecture", "Platform Strategy", "Cost Optimization", "Security and Governance"],
                },
                blocked_role_keywords={"testing": ["qa", "sdet", "tester"], "data": ["data scientist", "ml engineer"]},
                evidence_keywords={"testing": ["qa", "testing", "selenium"], "data": ["python", "pandas", "ml", "statistics"]},
                notes="Cloud roles need cloud/platform evidence; DevOps/SRE should be stretch unless CI/CD, Docker, Kubernetes or ops evidence exists.",
            ),
            RolePathRule(
                role_cluster="DevOps",
                role_family="Cloud / Infrastructure",
                signal_keywords=["devops", "ci/cd", "cicd", "jenkins", "docker", "kubernetes", "k8s", "terraform", "helm", "linux", "sre"],
                target_role_keywords=["devops", "sre", "platform", "infrastructure", "cloud"],
                target_roles_by_experience={
                    "entry": ["Junior DevOps Engineer", "Cloud Operations Engineer", "Build and Release Engineer"],
                    "mid": ["DevOps Engineer", "Cloud DevOps Engineer", "Build and Release Engineer", "Platform Engineer"],
                    "senior": ["Senior DevOps Engineer", "SRE Engineer", "Platform Engineer", "DevOps Lead"],
                    "lead": ["DevOps Lead", "SRE Lead", "Platform Engineering Lead", "DevOps Architect"],
                },
                stretch_roles_by_experience={"senior": ["Cloud Architect"], "lead": ["Principal Platform Engineer"]},
                skill_gaps_by_experience={
                    "entry": ["Linux", "Shell Scripting", "Docker", "CI/CD Basics"],
                    "mid": ["Kubernetes", "Terraform", "Monitoring", "Cloud Networking"],
                    "senior": ["SRE Practices", "Observability", "Security Basics", "Infrastructure Architecture"],
                    "lead": ["Platform Strategy", "Reliability Engineering", "Cost Optimization", "Team Leadership"],
                },
                blocked_role_keywords={"testing": ["qa", "sdet", "tester"], "backend": ["backend architect", "java technical lead"]},
                evidence_keywords={"testing": ["qa", "testing", "selenium"], "backend": ["java", "spring", "microservices"]},
                notes="DevOps users should progress via Linux, scripting, CI/CD, containers, cloud, Kubernetes, Terraform and reliability practices.",
            ),
            RolePathRule(
                role_cluster="Business Analysis",
                role_family="Business / Product",
                signal_keywords=["business analyst", "requirements", "user story", "user stories", "acceptance criteria", "stakeholder", "brd", "frd", "process mapping", "agile"],
                target_role_keywords=["business analyst", "product analyst", "functional consultant", "product owner", "data analyst"],
                target_roles_by_experience={
                    "entry": ["Junior Business Analyst", "Associate Business Analyst", "Functional Analyst"],
                    "mid": ["Business Analyst", "IT Business Analyst", "Functional Consultant", "Product Analyst"],
                    "senior": ["Senior Business Analyst", "Product Analyst", "Functional Consultant", "Product Owner"],
                    "lead": ["Lead Business Analyst", "Product Owner", "Business Consultant", "Product Operations Lead"],
                },
                stretch_roles_by_experience={"mid": ["Data Analyst"], "senior": ["Product Manager"], "lead": ["Product Manager"]},
                skill_gaps_by_experience={
                    "entry": ["Requirement Analysis", "User Stories", "Process Mapping"],
                    "mid": ["SQL Basics", "Product Metrics", "Dashboarding", "Stakeholder Management"],
                    "senior": ["Product Metrics", "Data-backed Decision Making", "Stakeholder Leadership", "Agile Delivery"],
                    "lead": ["Product Strategy", "Stakeholder Leadership", "Business Metrics", "Team Leadership"],
                },
                blocked_role_keywords={"backend": ["backend developer", "java developer"], "devops": ["devops", "sre"]},
                evidence_keywords={"backend": ["java", "spring", "api"], "devops": ["docker", "kubernetes", "cloud", "ci/cd"]},
                notes="BA profiles should not be forced into developer tracks without coding evidence; product/data-adjacent paths are often more realistic.",
            ),
            RolePathRule(
                role_cluster="Product Analysis",
                role_family="Business / Product",
                signal_keywords=["product analyst", "product owner", "product metrics", "funnel", "retention", "activation", "conversion", "experimentation", "roadmap"],
                target_role_keywords=["product analyst", "product owner", "product manager", "growth analyst", "business analyst"],
                target_roles_by_experience={
                    "entry": ["Associate Product Analyst", "Business Analyst", "Product Operations Analyst"],
                    "mid": ["Product Analyst", "Product Operations Analyst", "Business Analyst", "Growth Analyst"],
                    "senior": ["Senior Product Analyst", "Product Owner", "Growth Analyst", "Product Operations Manager"],
                    "lead": ["Lead Product Analyst", "Product Owner", "Product Manager", "Product Operations Lead"],
                },
                stretch_roles_by_experience={"senior": ["Product Manager"], "lead": ["Senior Product Manager"]},
                skill_gaps_by_experience={
                    "mid": ["SQL", "Product Metrics", "Dashboard Storytelling", "Experiment Analysis"],
                    "senior": ["Product Strategy", "Funnel Analysis", "Stakeholder Leadership", "Experimentation"],
                    "lead": ["Product Strategy", "Team Leadership", "Business Metrics", "Roadmap Prioritization"],
                },
                blocked_role_keywords={"backend": ["backend developer", "java developer"], "devops": ["devops", "sre"]},
                evidence_keywords={"backend": ["java", "spring", "api"], "devops": ["docker", "kubernetes", "cloud"]},
                notes="Product analysis profiles should move through product metrics, SQL, experimentation and stakeholder impact.",
            ),
            RolePathRule(
                role_cluster="Data Analytics",
                role_family="Data",
                signal_keywords=["data analyst", "analytics", "excel", "power bi", "tableau", "sql", "dashboard", "reporting", "dax"],
                target_role_keywords=["data analyst", "bi", "analytics", "data engineer", "product analyst"],
                target_roles_by_experience={
                    "entry": ["Junior Data Analyst", "MIS Analyst", "Reporting Analyst"],
                    "mid": ["Data Analyst", "BI Analyst", "Product Analyst", "Analytics Engineer"],
                    "senior": ["Senior Data Analyst", "BI Developer", "Analytics Engineer", "Data Engineer"],
                    "lead": ["Lead Data Analyst", "BI Lead", "Analytics Lead", "Data Analytics Manager"],
                },
                stretch_roles_by_experience={"mid": ["Data Engineer"], "senior": ["Analytics Engineer", "Data Scientist"], "lead": ["Data Science Manager"]},
                skill_gaps_by_experience={
                    "entry": ["SQL", "Excel Analysis", "Dashboard Basics"],
                    "mid": ["Python/Pandas", "Statistics", "Advanced SQL", "Dashboard Storytelling"],
                    "senior": ["Data Modeling", "Advanced DAX", "Python/Pandas", "Business Metrics"],
                    "lead": ["Analytics Strategy", "Data Modeling", "Stakeholder Leadership", "Business Metrics"],
                },
                blocked_role_keywords={"backend": ["backend developer", "java developer"], "devops": ["devops", "sre"]},
                evidence_keywords={"backend": ["java", "spring", "api"], "devops": ["docker", "kubernetes", "cloud"]},
                notes="Data analyst profiles should move through analytics/BI/data engineering or product analytics based on Python, SQL and modeling evidence.",
            ),
            RolePathRule(
                role_cluster="Data Engineering",
                role_family="Data",
                signal_keywords=["data engineer", "etl", "pipeline", "spark", "airflow", "databricks", "bigquery", "snowflake", "data warehouse"],
                target_role_keywords=["data engineer", "etl", "analytics engineer", "big data", "data platform"],
                target_roles_by_experience={
                    "entry": ["Junior Data Engineer", "ETL Developer", "Data Operations Engineer"],
                    "mid": ["Data Engineer", "ETL Developer", "Analytics Engineer", "Data Warehouse Developer"],
                    "senior": ["Senior Data Engineer", "Analytics Engineer", "Data Platform Engineer", "ETL Lead"],
                    "lead": ["Lead Data Engineer", "Data Architect", "Data Platform Lead", "Analytics Engineering Lead"],
                },
                stretch_roles_by_experience={"senior": ["Data Architect"], "lead": ["Principal Data Engineer"]},
                skill_gaps_by_experience={
                    "mid": ["Spark", "Airflow", "Data Modeling", "Cloud Data Warehouse"],
                    "senior": ["Data Architecture", "Streaming Pipelines", "Data Quality", "Cost Optimization"],
                    "lead": ["Data Platform Strategy", "Data Architecture", "Governance", "Team Leadership"],
                },
                blocked_role_keywords={"testing": ["qa", "sdet", "tester"]},
                evidence_keywords={"testing": ["qa", "testing", "selenium"]},
                notes="Data engineering users should be guided through pipelines, modeling, warehouses, streaming and platform ownership.",
            ),
            RolePathRule(
                role_cluster="Cyber Security",
                role_family="Security",
                signal_keywords=["cyber", "security", "soc", "siem", "incident response", "vulnerability", "threat", "iam", "gRC"],
                target_role_keywords=["security", "soc", "cyber", "iam", "grc", "cloud security"],
                target_roles_by_experience={
                    "entry": ["SOC Analyst", "Junior Security Analyst", "Security Operations Associate"],
                    "mid": ["Security Analyst", "SOC L2 Analyst", "Vulnerability Analyst", "IAM Analyst"],
                    "senior": ["Senior Security Analyst", "SOC Lead", "Cloud Security Analyst", "Incident Response Analyst"],
                    "lead": ["Security Lead", "SOC Manager", "Cloud Security Lead", "GRC Lead"],
                },
                stretch_roles_by_experience={"senior": ["Security Engineer"], "lead": ["Security Architect"]},
                skill_gaps_by_experience={
                    "entry": ["Networking Basics", "Linux Basics", "SIEM Basics"],
                    "mid": ["Incident Response", "Vulnerability Assessment", "Scripting Basics", "Cloud Security Basics"],
                    "senior": ["Threat Hunting", "Cloud Security", "Incident Leadership", "Security Automation"],
                    "lead": ["Security Strategy", "Incident Leadership", "Governance", "Cloud Security Architecture"],
                },
                blocked_role_keywords={"backend": ["backend developer", "java developer"], "data": ["data scientist", "ml engineer"]},
                evidence_keywords={"backend": ["java", "spring", "api"], "data": ["python", "statistics", "ml", "pandas"]},
                notes="Security paths should not jump to software/data roles unless matching technical evidence exists.",
            ),
            RolePathRule(
                role_cluster="Database",
                role_family="Database / Data",
                signal_keywords=["database", "dba", "sql developer", "oracle", "pl/sql", "stored procedure", "query optimization", "indexing"],
                target_role_keywords=["database", "dba", "sql developer", "data engineer", "etl", "bi"],
                target_roles_by_experience={
                    "entry": ["SQL Developer", "Junior DBA", "Database Support Engineer"],
                    "mid": ["Database Developer", "DBA", "SQL Developer", "ETL Developer"],
                    "senior": ["Senior Database Developer", "Senior DBA", "Data Engineer", "Database Performance Engineer"],
                    "lead": ["Database Lead", "Data Architect", "DBA Lead", "Data Engineering Lead"],
                },
                stretch_roles_by_experience={"senior": ["Data Engineer"], "lead": ["Data Architect"]},
                skill_gaps_by_experience={
                    "entry": ["Advanced SQL", "Indexing", "Stored Procedures"],
                    "mid": ["Query Optimization", "Data Modeling", "ETL Basics", "Performance Tuning"],
                    "senior": ["Data Architecture", "Cloud Database", "ETL/Data Engineering", "Performance Strategy"],
                    "lead": ["Data Architecture", "Database Strategy", "Migration Planning", "Team Leadership"],
                },
                blocked_role_keywords={"testing": ["qa", "sdet", "tester"], "frontend": ["frontend", "react"]},
                evidence_keywords={"testing": ["qa", "testing", "selenium"], "frontend": ["react", "javascript", "typescript"]},
                notes="Database users can bridge to data engineering, BI, performance engineering or architecture with evidence.",
            ),
            RolePathRule(
                role_cluster="Mobile Engineering",
                role_family="Software Engineering",
                signal_keywords=["android", "ios", "mobile", "kotlin", "swift", "react native", "flutter"],
                target_role_keywords=["android", "ios", "mobile", "flutter", "react native"],
                target_roles_by_experience={
                    "entry": ["Junior Android Developer", "Mobile Developer", "iOS Developer"],
                    "mid": ["Mobile Engineer", "Android Developer", "iOS Developer", "React Native Developer"],
                    "senior": ["Senior Mobile Engineer", "Lead Android Engineer", "Mobile Platform Engineer", "Senior iOS Engineer"],
                    "lead": ["Mobile Lead", "Mobile Architect", "Platform Mobile Lead", "Engineering Lead - Mobile"],
                },
                stretch_roles_by_experience={"senior": ["Mobile Architect"], "lead": ["Principal Mobile Engineer"]},
                skill_gaps_by_experience={
                    "mid": ["App Architecture", "Performance Optimization", "Testing", "API Integration"],
                    "senior": ["Mobile Architecture", "CI/CD for Mobile", "Performance", "Modularization"],
                    "lead": ["Mobile Architecture", "Platform Strategy", "Mentoring", "Release Ownership"],
                },
                blocked_role_keywords={"testing": ["qa", "sdet", "tester"], "backend": ["backend architect"]},
                evidence_keywords={"testing": ["qa", "testing", "appium"], "backend": ["java", "spring", "api"]},
                notes="Mobile engineers should move through mobile depth, app architecture, platform and lead tracks.",
            ),
            RolePathRule(
                role_cluster="Agile Delivery",
                role_family="Project / Delivery",
                signal_keywords=["scrum master", "agile", "delivery manager", "project manager", "jira", "sprint", "kanban", "safe"],
                target_role_keywords=["scrum master", "agile", "delivery", "project", "program", "product owner"],
                target_roles_by_experience={
                    "entry": ["Project Coordinator", "Associate Scrum Master", "Agile Team Coordinator"],
                    "mid": ["Scrum Master", "Agile Coordinator", "Project Manager", "Delivery Analyst"],
                    "senior": ["Senior Scrum Master", "Agile Coach", "Delivery Manager", "Project Manager"],
                    "lead": ["Agile Coach", "Delivery Lead", "Program Manager", "Senior Project Manager"],
                },
                stretch_roles_by_experience={"senior": ["Product Owner"], "lead": ["Portfolio Manager"]},
                skill_gaps_by_experience={
                    "mid": ["Agile Metrics", "Stakeholder Management", "Risk Management", "Delivery Reporting"],
                    "senior": ["Agile Coaching", "Program Delivery", "Stakeholder Leadership", "Metrics-driven Delivery"],
                    "lead": ["Portfolio Delivery", "Executive Stakeholder Management", "Program Governance", "Team Leadership"],
                },
                blocked_role_keywords={"backend": ["backend developer", "java developer"], "devops": ["devops", "sre"]},
                evidence_keywords={"backend": ["java", "spring", "api"], "devops": ["docker", "kubernetes", "cloud"]},
                notes="Agile delivery roles should not be converted into developer paths without technical evidence.",
            ),
            RolePathRule(
                role_cluster="Enterprise Platforms",
                role_family="Enterprise Applications",
                signal_keywords=["salesforce", "servicenow", "sap", "oracle apps", "workday", "crm", "erp", "functional consultant"],
                target_role_keywords=["salesforce", "servicenow", "sap", "erp", "crm", "functional consultant", "technical consultant"],
                target_roles_by_experience={
                    "entry": ["Platform Support Analyst", "Junior Functional Consultant", "CRM Analyst"],
                    "mid": ["Functional Consultant", "Platform Consultant", "Salesforce Consultant", "ServiceNow Consultant"],
                    "senior": ["Senior Functional Consultant", "Technical Consultant", "Solution Consultant", "Platform Lead"],
                    "lead": ["Solution Architect", "Platform Architect", "Lead Consultant", "Practice Lead"],
                },
                stretch_roles_by_experience={"senior": ["Solution Architect"], "lead": ["Enterprise Architect"]},
                skill_gaps_by_experience={
                    "mid": ["Platform Configuration", "Integration Basics", "Business Process Mapping", "Reporting"],
                    "senior": ["Platform Architecture", "Integration Design", "Stakeholder Leadership", "Automation"],
                    "lead": ["Solution Architecture", "Governance", "Integration Strategy", "Practice Leadership"],
                },
                blocked_role_keywords={"backend": ["backend developer", "java developer"], "testing": ["qa", "sdet"]},
                evidence_keywords={"backend": ["java", "spring", "api"], "testing": ["qa", "testing", "selenium"]},
                notes="Enterprise platform users should move through functional/technical consultant, platform lead or solution architecture based on platform evidence.",
            ),
            self._fallback_general_rule(),
        ]

    def _fallback_general_rule(self) -> RolePathRule:
        return RolePathRule(
            role_cluster="General IT",
            role_family="General IT",
            signal_keywords=["it", "technology", "software", "support", "analyst", "engineer"],
            target_role_keywords=["engineer", "analyst", "specialist", "consultant", "lead"],
            target_roles_by_experience={
                "entry": ["IT Support Engineer", "Associate Analyst", "Junior Software Engineer"],
                "mid": ["IT Specialist", "Technical Analyst", "Software Engineer", "Application Support Engineer"],
                "senior": ["Senior IT Specialist", "Technical Specialist", "Senior Analyst", "Technical Lead"],
                "lead": ["Technical Lead", "Solution Consultant", "IT Lead", "Senior Technical Specialist"],
            },
            stretch_roles_by_experience={"senior": ["Solution Architect"], "lead": ["Solution Architect"]},
            skill_gaps_by_experience={
                "entry": ["Role-specific Foundations", "SQL Basics", "Communication for IT"],
                "mid": ["Role-specific Depth", "Automation Basics", "Cloud Fundamentals"],
                "senior": ["Technical Ownership", "Role-specific Advanced Skills", "Impact Storytelling"],
                "lead": ["Technical Leadership", "Architecture Basics", "Stakeholder Management"],
            },
            blocked_role_keywords={},
            evidence_keywords={},
            notes="Fallback rule used only when a specific IT family is unclear.",
        )

    def _score_rule(self, profile_text: str, rule: Optional[RolePathRule]) -> int:
        if not rule:
            return 0
        normalized = self._normalize_text(profile_text)
        return sum(1 for keyword in rule.signal_keywords if self._normalize_text(keyword) in normalized)

    def _find_rule(self, role_cluster: str, rules: List[RolePathRule]) -> Optional[RolePathRule]:
        normalized = self._normalize_text(role_cluster)
        for rule in rules:
            if self._normalize_text(rule.role_cluster) == normalized:
                return rule

        # Alias support for common legacy cluster names.
        aliases = {
            "testing qa": "testing qa",
            "testing/qa": "testing qa",
            "qa": "testing qa",
            "data ai": "data analytics",
            "business analyst": "business analysis",
            "production support": "application support",
        }
        alias = aliases.get(normalized)
        if alias:
            for rule in rules:
                if self._normalize_text(rule.role_cluster) == alias:
                    return rule
        return None

    def _band_values(self, band_map: Dict[str, Any], band: str) -> List[str]:
        if not isinstance(band_map, dict):
            return []
        value = band_map.get(band) or band_map.get("senior") or band_map.get("mid") or []
        return self._normalize_list(value)

    def _select_skill_gaps(
        self,
        profile: CareerProfileRequest,
        rule: RolePathRule,
        band: str,
    ) -> List[str]:
        configured = self._band_values(rule.skill_gaps_by_experience, band)
        missing = [skill for skill in configured if not self._is_skill_already_present(skill, profile)]
        return (missing or configured)[:4]

    def _is_skill_already_present(self, skill: str, profile: CareerProfileRequest) -> bool:
        text = self._profile_text(profile)
        normalized_skill = self._normalize_text(skill)

        # Only skip when a clear core term is already present. Composite skills
        # like "AWS / Cloud Deployment" are treated as missing unless a clear
        # evidence term exists.
        key_terms = [term.strip() for term in normalized_skill.replace("/", " ").split() if len(term.strip()) >= 4]
        if not key_terms:
            return False

        matched_terms = [term for term in key_terms if term in text]
        return len(matched_terms) >= max(1, min(2, len(key_terms)))

    def _build_prompt_context(
        self,
        profile: CareerProfileRequest,
        rule: RolePathRule,
        original_cluster: str,
        final_cluster: str,
        band: str,
        allowed_roles: List[str],
        stretch_roles: List[str],
        skill_gaps: List[str],
    ) -> str:
        return f"""
ROLE GUARDRAIL DECISION FROM PRODUCT ENGINE:
- Rule source: {self._rule_source}
- Original mapped cluster: {original_cluster}
- Final product-approved cluster: {final_cluster}
- Career family: {rule.role_family}
- Experience band: {band}
- Allowed target roles: {allowed_roles}
- Stretch target roles: {stretch_roles}
- Priority skill gaps: {skill_gaps}
- Rule notes: {rule.notes}

IMPORTANT:
Use the final product-approved cluster and allowed target roles as the source of truth.
Do not suggest roles outside this family unless the user has strong evidence for that family.
If the role is a stretch, mark it as Medium/Low fit and explain the missing evidence.
""".strip()

    def _experience_band(self, experience_years: Any) -> str:
        experience = self._safe_float(experience_years)
        if experience < 2:
            return "entry"
        if experience < 5:
            return "mid"
        if experience < 8:
            return "senior"
        return "lead"

    def _matches_any_role(self, role_text: str, roles: List[str]) -> bool:
        normalized = self._normalize_text(role_text)
        for role in roles:
            rule_role = self._normalize_text(role)
            if not rule_role:
                continue
            if normalized == rule_role:
                return True
            if normalized in rule_role or rule_role in normalized:
                return True
        return False

    def _canonicalize_role(self, role: str, canonical_roles: List[str]) -> str:
        role_text = self._normalize_text(role)
        for canonical in canonical_roles:
            canonical_text = self._normalize_text(canonical)
            if role_text == canonical_text or role_text in canonical_text or canonical_text in role_text:
                return canonical
        return role

    def _json_list(self, value: Any) -> List[str]:
        parsed = self._safe_json(value)
        return self._normalize_list(parsed)

    def _json_dict(self, value: Any) -> Dict[str, Any]:
        parsed = self._safe_json(value)
        return parsed if isinstance(parsed, dict) else {}

    def _safe_json(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    def _profile_text(self, profile: CareerProfileRequest) -> str:
        return self._normalize_text(
            f"{profile.current_role} {' '.join(profile.skills or [])} {profile.goal}"
        )

    def _normalize_text(self, value: Any) -> str:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        return str(value or "").lower().replace("_", " ").replace("-", " ").strip()

    def _normalize_list(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []

        result = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
            elif isinstance(item, dict):
                label = (
                    item.get("target_role")
                    or item.get("role")
                    or item.get("skill_name")
                    or item.get("path_name")
                    or item.get("name")
                )
                if label:
                    result.append(str(label).strip())
        return self._dedupe_keep_order(result)

    def _dedupe_keep_order(self, values: List[str]) -> List[str]:
        seen = set()
        result = []
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            key = self._normalize_text(text)
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    def _has_any(self, text: str, terms: List[str]) -> bool:
        normalized = self._normalize_text(text)
        return any(self._normalize_text(term) in normalized for term in terms if str(term).strip())

    def _format_phrase(self, values: List[str], fallback: str) -> str:
        cleaned = self._dedupe_keep_order(values)
        if not cleaned:
            return fallback
        if len(cleaned) == 1:
            return cleaned[0]
        if len(cleaned) == 2:
            return f"{cleaned[0]} and {cleaned[1]}"
        return f"{cleaned[0]}, {cleaned[1]} and {cleaned[2]}"

    def _safe_text(self, value: Any, fallback: str) -> str:
        if value is None:
            return fallback
        text = str(value).strip()
        return text if text else fallback

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value or 0)
        except Exception:
            return 0.0
