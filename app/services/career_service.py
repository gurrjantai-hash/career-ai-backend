from typing import Any, Dict, List

from app.models import CareerProfileRequest, CareerAnalysisResponse, SalaryInsight, TargetSalaryInsight
from app.services.ai_service import AIService
from app.services.salary_service import SalaryService
from app.services.db_service import DBService
from app.services.role_intelligence_service import RoleIntelligenceService
from app.services.skill_premium_service import SkillPremiumService
from app.services.role_guardrail_service import RoleGuardrailService, RoleGuardrailDecision
from app.prompts.career_prompts import career_analysis_prompt


class CareerService:

    def __init__(self):
        self.ai_service = AIService()
        self.salary_service = SalaryService()
        self.db_service = DBService()
        self.role_intelligence_service = RoleIntelligenceService()
        self.skill_premium_service = SkillPremiumService()
        self.role_guardrail_service = RoleGuardrailService()

    def analyze(
        self,
        profile: CareerProfileRequest,
        user_id: str,
    ) -> CareerAnalysisResponse:
        role_intelligence = self.role_intelligence_service.map_role(profile)
        suggested_role_cluster = role_intelligence.primary_cluster

        if role_intelligence.confidence == "Low" and suggested_role_cluster == "General IT":
            ai_cluster = self.ai_service.classify_role_cluster(
                profile.current_role,
                profile.skills,
            )

            if ai_cluster:
                suggested_role_cluster = self._normalize_ai_cluster(ai_cluster)

        guardrail_decision = self.role_guardrail_service.build_decision(
            profile=profile,
            suggested_role_cluster=suggested_role_cluster,
        )
        role_cluster = guardrail_decision.final_role_cluster

        salary = self.salary_service.calculate_salary(
            profile=profile,
            role_cluster=role_cluster,
            role_intelligence=role_intelligence,
        )

        salary = self._post_process_salary_insight(
            salary=salary,
            profile=profile,
            role_cluster=role_cluster,
        )

        role_intelligence_context = self.role_intelligence_service.build_prompt_context(
            role_intelligence
        )

        prompt = career_analysis_prompt(
            profile=profile,
            salary=salary,
            role_cluster=role_cluster,
            role_intelligence_context=role_intelligence_context,
            role_guardrail_context=guardrail_decision.prompt_context,
            allowed_target_roles=guardrail_decision.allowed_target_roles,
            stretch_target_roles=guardrail_decision.stretch_target_roles,
            priority_skill_gaps=guardrail_decision.priority_skill_gaps,
        )

        ai_result = self.ai_service.get_json_response(prompt)

        guarded_result = self.role_guardrail_service.validate_ai_result(
            ai_result=ai_result,
            profile=profile,
            decision=guardrail_decision,
        )

        target_roles = self._refine_target_role_order(
            target_roles=guarded_result["target_roles"],
            role_cluster=role_cluster,
            profile=profile,
        )
        top_skill_gaps = guarded_result["top_skill_gaps"]
        growth_paths = guarded_result["growth_paths"]

        target_salary_insights = self.salary_service.calculate_target_salary_insights(
            profile=profile,
            growth_paths=growth_paths,
            target_roles=target_roles,
        )

        target_salary_insights = self._post_process_target_salary_insights(
            target_salary_insights=target_salary_insights,
            target_roles=target_roles,
            role_cluster=role_cluster,
            profile=profile,
        )

        skill_premium_insights = self.skill_premium_service.get_skill_premium_insights(
            role_cluster=role_cluster,
            top_skill_gaps=top_skill_gaps,
            target_roles=target_roles,
            limit=6,
            experience_years=profile.experience_years,
            career_goal=profile.goal,
        )

        response = CareerAnalysisResponse(
            role_cluster=role_cluster,
            current_level=self.role_guardrail_service.current_level_from_experience(
                profile.experience_years
            ),

            summary=guarded_result["summary"],
            recommended_next_move=self._refine_recommended_next_move(
                recommended_next_move=guarded_result["recommended_next_move"],
                role_cluster=role_cluster,
                target_roles=target_roles,
                profile=profile,
            ),
            goal_strategy=guarded_result["goal_strategy"],

            salary_insight=salary,
            target_salary_insights=target_salary_insights,
            target_roles=target_roles,
            top_skill_gaps=top_skill_gaps,
            skill_salary_impact=guarded_result["skill_salary_impact"],
            skill_premium_insights=skill_premium_insights,

            growth_paths=growth_paths,
            why_recommendations=guarded_result["why_recommendations"],

            roadmap_4_weeks=guarded_result["roadmap_4_weeks"],
            resume_suggestions=guarded_result["resume_suggestions"],

            confidence_notes=self._add_role_intelligence_confidence_note(
                notes=guarded_result["confidence_notes"],
                role_intelligence=role_intelligence,
                decision=guardrail_decision,
            ),

            disclaimer="This is an AI-assisted estimate based on your profile and market patterns. It is not a guaranteed salary prediction.",
        )

        analysis_id = self.db_service.save_career_analysis(
            profile=profile,
            response=response,
            user_id=user_id,
        )

        if analysis_id:
            response.analysis_id = analysis_id

        return response

    def _post_process_salary_insight(
        self,
        salary: SalaryInsight,
        profile: CareerProfileRequest,
        role_cluster: str,
    ) -> SalaryInsight:
        """
        Quick product correction for non-IT BPO/customer support salary estimates.
        The generic salary engine can overestimate these roles until salary_bands_v2
        gets full BPO-specific coverage.
        """
        if role_cluster != "Customer Support / BPO":
            return salary

        experience = self._safe_float(profile.experience_years)
        city_factor = self._city_factor(profile.city)

        if experience < 2:
            base_min, base_max = 2.0, 4.0
            band_label = "Entry"
        elif experience < 5:
            base_min, base_max = 3.0, 6.5
            band_label = "Early/Mid"
        elif experience < 8:
            base_min, base_max = 4.0, 8.0
            band_label = "Mid/Senior"
        else:
            base_min, base_max = 5.0, 10.0
            band_label = "Senior"

        market_min = round(base_min * city_factor, 1)
        market_max = round(base_max * city_factor, 1)
        current_salary = self._safe_float(profile.current_salary_lpa)

        if current_salary < market_min:
            gap = f"+{round(market_min - current_salary, 1)}L to +{round(market_max - current_salary, 1)}L"
        elif current_salary > market_max:
            gap = "You are above this broad market estimate"
        else:
            gap = "You are within the expected market range"

        return SalaryInsight(
            current_salary_lpa=current_salary,
            market_min_lpa=market_min,
            market_max_lpa=market_max,
            salary_gap_lpa=gap,
            confidence=(
                f"Medium - BPO/customer support salary estimate based on experience band "
                f"({band_label}), city factor ({profile.city}), and broad Indian market patterns. "
                f"Premium processes, international support, team lead, or customer success roles can pay higher."
            ),
        )

    def _post_process_target_salary_insights(
        self,
        target_salary_insights: List[TargetSalaryInsight],
        target_roles: List[str],
        role_cluster: str,
        profile: CareerProfileRequest,
    ) -> List[TargetSalaryInsight]:
        cleaned = []
        for insight in target_salary_insights or []:
            if role_cluster == "API Testing" and "sdet" in insight.target_role.lower():
                if not self._has_strong_sdet_evidence(profile):
                    cleaned.append(
                        TargetSalaryInsight(
                            target_role=insight.target_role,
                            estimated_min_lpa=insight.estimated_min_lpa,
                            estimated_max_lpa=insight.estimated_max_lpa,
                            fit_level="Medium",
                            salary_upside_note=(
                                f"{insight.target_role} is a possible stretch path from API testing, "
                                "but should be treated as Medium fit until stronger programming, framework design, "
                                "and CI automation proof is available."
                            ),
                        )
                    )
                    continue
            cleaned.append(insight)

        if role_cluster == "Customer Support / BPO":
            return self._bpo_target_salary_insights(target_roles=target_roles, profile=profile)

        if role_cluster == "API Testing":
            return self._api_testing_target_salary_insights(
                target_salary_insights=cleaned,
                target_roles=target_roles,
                profile=profile,
            )

        return cleaned

    def _api_testing_target_salary_insights(
        self,
        target_salary_insights: List[TargetSalaryInsight],
        target_roles: List[str],
        profile: CareerProfileRequest,
    ) -> List[TargetSalaryInsight]:
        """
        API testing has a common product edge case:
        the salary service can bring SDET/SDET Lead from stretch growth paths even
        when the guarded target roles do not include them. For API testers with
        only basic coding evidence, keep target salary focused on Senior API
        Tester, API Automation Engineer and QA Lead.
        """
        target_keys = {self._normalize_role_key(role) for role in (target_roles or [])}
        strong_sdet = self._has_strong_sdet_evidence(profile)

        insights: List[TargetSalaryInsight] = []
        for insight in target_salary_insights or []:
            role_key = self._normalize_role_key(insight.target_role)
            is_sdet_role = "sdet" in role_key

            # Do not show SDET Lead/SDET salary cards unless they are actual
            # guarded target roles or the user has strong automation/coding proof.
            if is_sdet_role and not strong_sdet and role_key not in target_keys:
                continue

            # For API Testing, target salary cards should mirror the final target
            # roles. Avoid salary cards for hidden/stretch-only roles.
            if role_key not in target_keys and not (is_sdet_role and strong_sdet):
                continue

            if is_sdet_role and not strong_sdet:
                insights.append(
                    TargetSalaryInsight(
                        target_role=insight.target_role,
                        estimated_min_lpa=insight.estimated_min_lpa,
                        estimated_max_lpa=insight.estimated_max_lpa,
                        fit_level="Medium",
                        salary_upside_note=(
                            f"{insight.target_role} is a possible stretch path from API testing, "
                            "but should be treated as Medium fit until stronger programming, framework design, "
                            "and CI automation proof is available."
                        ),
                    )
                )
            else:
                insights.append(insight)

        # Fill useful API Testing salary cards if the salary service did not have
        # a direct band for the final guarded target role.
        existing_keys = {self._normalize_role_key(item.target_role) for item in insights}
        city_factor = self._city_factor(profile.city)
        fallback_map = {
            "Senior API Tester": (12.1, 25.3, "High"),
            "API Automation Engineer": (12.1, 25.3, "High"),
            "QA Lead": (12.0, 24.0, "Medium"),
            "SDET": (19.8, 41.8, "High" if strong_sdet else "Medium"),
        }

        for role in target_roles or []:
            role_key = self._normalize_role_key(role)
            if role_key in existing_keys or role not in fallback_map:
                continue
            base_min, base_max, fit = fallback_map[role]
            if "sdet" in role_key and not strong_sdet:
                note = (
                    f"{role} is a stretch path from API testing. Treat this as Medium fit until "
                    "you have stronger programming, automation framework and CI proof."
                )
            else:
                note = (
                    f"{role} is estimated from API testing / quality engineering salary patterns for {profile.city}. "
                    "Actual salary depends on automation depth, API complexity, CI exposure, company tier and interview performance."
                )
            insights.append(
                TargetSalaryInsight(
                    target_role=role,
                    estimated_min_lpa=round(base_min * city_factor, 1),
                    estimated_max_lpa=round(base_max * city_factor, 1),
                    fit_level=fit,
                    salary_upside_note=note,
                )
            )
            existing_keys.add(role_key)

        return insights[:4]

    def _bpo_target_salary_insights(
        self,
        target_roles: List[str],
        profile: CareerProfileRequest,
    ) -> List[TargetSalaryInsight]:
        city_factor = self._city_factor(profile.city)
        salary_map = {
            "Senior Customer Support Executive": (3.5, 6.5, "High"),
            "Process Specialist": (4.0, 7.0, "High"),
            "Escalation Specialist": (4.5, 8.0, "High"),
            "Customer Experience Associate": (3.5, 6.5, "High"),
            "Quality Analyst - BPO": (4.0, 7.5, "Medium"),
            "Team Leader - Customer Support": (5.5, 10.0, "Medium"),
            "Customer Success Associate": (5.0, 9.0, "Medium"),
        }

        insights: List[TargetSalaryInsight] = []
        roles = self._dedupe_keep_order(target_roles + ["Quality Analyst - BPO", "Team Leader - Customer Support"])

        for role in roles:
            if role not in salary_map:
                continue
            base_min, base_max, fit = salary_map[role]
            min_lpa = round(base_min * city_factor, 1)
            max_lpa = round(base_max * city_factor, 1)
            insights.append(
                TargetSalaryInsight(
                    target_role=role,
                    estimated_min_lpa=min_lpa,
                    estimated_max_lpa=max_lpa,
                    fit_level=fit,
                    salary_upside_note=(
                        f"{role} is estimated from BPO/customer support salary patterns for {profile.city}. "
                        "Actual salary depends on process type, communication quality, shift, domain, metrics, and company tier."
                    ),
                )
            )
            if len(insights) >= 4:
                break

        return insights

    def _refine_target_role_order(
        self,
        target_roles: List[str],
        role_cluster: str,
        profile: CareerProfileRequest,
    ) -> List[str]:
        roles = self._dedupe_keep_order(target_roles or [])
        experience = self._safe_float(profile.experience_years)

        if role_cluster == "Backend Engineering" and experience >= 10:
            preferred = [
                "Lead Backend Engineer",
                "Technical Lead - Backend",
                "Java Technical Lead",
                "Backend Architect",
                "Senior Backend Engineer",
            ]
            ordered = [role for role in preferred if self._role_exists(role, roles)]
            ordered += [role for role in roles if not self._role_exists(role, ordered)]
            return ordered[:4]

        return roles[:4]

    def _refine_recommended_next_move(
        self,
        recommended_next_move: str,
        role_cluster: str,
        target_roles: List[str],
        profile: CareerProfileRequest,
    ) -> str:
        if role_cluster == "Backend Engineering" and self._safe_float(profile.experience_years) >= 10:
            primary = target_roles[0] if target_roles else "Lead Backend Engineer"
            secondary = target_roles[1] if len(target_roles) > 1 else "Technical Lead - Backend"
            return (
                f"Focus on {primary} / {secondary} positioning: show system design ownership, "
                "architecture decisions, mentoring, delivery ownership, and measurable backend impact."
            )
        return recommended_next_move

    def _has_strong_sdet_evidence(self, profile: CareerProfileRequest) -> bool:
        text = self._profile_text(profile)
        weak_basic_only = any(term in text for term in ["basic java", "basic python", "java basics", "python basics"])
        strong_terms = [
            "selenium", "restassured", "rest assured", "automation framework", "framework design",
            "page object", "testng", "junit", "cypress", "playwright", "ci/cd", "jenkins",
            "coding", "programming", "java automation", "python automation"
        ]
        coding_terms = ["java", "python", "c#", "javascript", "typescript"]
        has_strong_tooling = any(term in text for term in strong_terms)
        has_coding = any(term in text for term in coding_terms)
        return has_strong_tooling and has_coding and not (weak_basic_only and not has_strong_tooling)

    def _profile_text(self, profile: CareerProfileRequest) -> str:
        return " ".join([
            str(profile.current_role or ""),
            str(profile.city or ""),
            str(profile.goal or ""),
            " ".join([str(skill or "") for skill in (profile.skills or [])]),
        ]).lower()

    def _city_factor(self, city: str) -> float:
        normalized = str(city or "").strip().lower()
        if normalized in ["bengaluru", "bangalore", "mumbai", "gurugram", "gurgaon", "hyderabad", "pune", "noida", "delhi", "ncr"]:
            return 1.0
        if normalized in ["chennai", "kolkata", "ahmedabad", "jaipur", "chandigarh", "indore", "kochi"]:
            return 0.9
        return 0.85

    def _safe_float(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    def _role_exists(self, role: str, roles: List[str]) -> bool:
        normalized = role.lower().strip()
        return any(existing.lower().strip() == normalized for existing in roles)

    def _normalize_role_key(self, role: str) -> str:
        return " ".join(str(role or "").lower().replace("-", " ").split())

    def _add_role_intelligence_confidence_note(
        self,
        notes: List[str],
        role_intelligence,
        decision: RoleGuardrailDecision,
    ) -> List[str]:
        """
        Confidence notes are useful for us, but they should not confuse users.
        If role intelligence initially mapped the user to a different/wrong cluster
        and the guardrail corrected it, do not show the wrong mapped role in the UI.
        """
        merged: List[str] = []

        if role_intelligence.primary_cluster == decision.final_role_cluster:
            merged.append(
                f"Role mapping: your input role was matched under "
                f"'{decision.final_role_cluster}' with "
                f"{role_intelligence.confidence.lower()} confidence."
            )
        else:
            merged.append(
                f"Role mapping: your input role and skills were finally validated as "
                f"'{decision.final_role_cluster}' after product guardrails checked the stronger evidence."
            )

        merged.append(
            f"Final career direction was validated by product guardrails with allowed target roles: "
            f"{', '.join(decision.allowed_target_roles)}."
        )

        for note in notes:
            if not isinstance(note, str) or not note.strip():
                continue

            note_text = note.strip()

            # Hide internal correction details like "Production Support -> Customer Support / BPO".
            if "Role cluster was corrected from" in note_text:
                continue
            if "Product guardrail:" in note_text:
                continue
            if "Role mapping:" in note_text and role_intelligence.primary_cluster != decision.final_role_cluster:
                continue

            merged.append(note_text)

        return self._dedupe_keep_order(merged)

    def _dedupe_keep_order(self, values: List[str]) -> List[str]:
        seen = set()
        result = []
        for value in values:
            text = str(value).strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
        return result

    def _normalize_ai_cluster(self, ai_cluster: str) -> str:
        normalized = ai_cluster.strip().lower()

        cluster_mapping = {
            "operations": "General IT",
            "it operations": "General IT",
            "software": "General IT",
            "technology": "General IT",
            "support": "Application Support",
            "application support": "Application Support",
            "production support": "Application Support",
            "qa": "Testing/QA",
            "testing": "Testing/QA",
            "quality assurance": "Testing/QA",
            "sdet": "Testing/QA",
            "frontend": "Frontend Engineering",
            "backend": "Backend Engineering",
            "full stack": "Full Stack Engineering",
            "devops": "DevOps",
            "cloud": "Cloud Engineering",
            "data": "Data Analytics",
            "business analysis": "Business Analysis",
            "business analyst": "Business Analysis",
            "security": "Cyber Security",
            "cyber security": "Cyber Security",
            "database": "Database",
            "dba": "Database",
            "mobile": "Mobile Engineering",
            "agile": "Agile Delivery",
            "project management": "Agile Delivery",
            "enterprise platform": "Enterprise Platforms",
        }

        return cluster_mapping.get(normalized, "General IT")
