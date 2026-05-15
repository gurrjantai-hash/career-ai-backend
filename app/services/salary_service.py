import os
from typing import Any, Dict, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from app.models import CareerProfileRequest, SalaryInsight, RoleIntelligenceResult

load_dotenv()


class SalaryService:
    """
    Phase 2B Salary Engine v2.

    This is still an estimate engine, not a guaranteed salary predictor.

    It uses:
    - primary cluster / canonical role
    - experience band
    - city multiplier
    - role intelligence confidence
    - skill readiness
    """

    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")

    def calculate_salary(
        self,
        profile: CareerProfileRequest,
        role_cluster: str,
        role_intelligence: Optional[RoleIntelligenceResult] = None
    ) -> SalaryInsight:

        salary_band = self._load_salary_band(role_cluster, role_intelligence)

        if not salary_band:
            return self._fallback_salary(profile, role_cluster)

        base_min, base_max, experience_label = self._select_experience_band(
            profile.experience_years,
            salary_band
        )

        city_multiplier = self._city_multiplier(profile.city)

        skill_multiplier = self._skill_readiness_multiplier(role_intelligence)

        market_min = base_min * city_multiplier
        market_max = base_max * city_multiplier * skill_multiplier

        market_min = round(market_min, 1)
        market_max = round(market_max, 1)

        if market_max < market_min:
            market_max = market_min + 1

        salary_gap = self._calculate_salary_gap(
            profile.current_salary_lpa,
            market_min,
            market_max
        )

        confidence = self._build_confidence_note(
            salary_band=salary_band,
            role_intelligence=role_intelligence,
            experience_label=experience_label,
            city=profile.city,
            skill_multiplier=skill_multiplier
        )

        return SalaryInsight(
            current_salary_lpa=profile.current_salary_lpa,
            market_min_lpa=market_min,
            market_max_lpa=market_max,
            salary_gap_lpa=salary_gap,
            confidence=confidence
        )

    def _load_salary_band(
        self,
        role_cluster: str,
        role_intelligence: Optional[RoleIntelligenceResult]
    ) -> Optional[Dict[str, Any]]:

        if not self.database_url:
            return None

        primary_cluster = role_cluster

        if role_intelligence and role_intelligence.primary_cluster:
            primary_cluster = role_intelligence.primary_cluster

        try:
            connection = psycopg2.connect(self.database_url)
            cursor = connection.cursor(cursor_factory=RealDictCursor)

            cursor.execute(
                """
                select *
                from salary_bands_v2
                where lower(role_key) = lower(%s)
                   or lower(primary_cluster) = lower(%s)
                order by
                  case
                    when lower(role_key) = lower(%s) then 1
                    when lower(primary_cluster) = lower(%s) then 2
                    else 3
                  end
                limit 1
                """,
                (
                    primary_cluster,
                    primary_cluster,
                    primary_cluster,
                    primary_cluster
                )
            )

            row = cursor.fetchone()

            cursor.close()
            connection.close()

            if row:
                return dict(row)

            return None

        except Exception as e:
            print(f"Failed to load salary band v2: {e}")
            return None

    def _select_experience_band(
        self,
        experience_years: float,
        salary_band: Dict[str, Any]
    ) -> Tuple[float, float, str]:

        if experience_years < 2:
            return (
                float(salary_band["entry_min_lpa"]),
                float(salary_band["entry_max_lpa"]),
                "Entry"
            )

        if experience_years < 5:
            return (
                float(salary_band["early_min_lpa"]),
                float(salary_band["early_max_lpa"]),
                "Early/Mid"
            )

        if experience_years < 8:
            return (
                float(salary_band["mid_min_lpa"]),
                float(salary_band["mid_max_lpa"]),
                "Mid/Senior"
            )

        if experience_years < 12:
            return (
                float(salary_band["senior_min_lpa"]),
                float(salary_band["senior_max_lpa"]),
                "Senior/Lead"
            )

        return (
            float(salary_band["lead_min_lpa"]),
            float(salary_band["lead_max_lpa"]),
            "Lead+"
        )

    def _city_multiplier(self, city: str) -> float:
        normalized_city = city.strip().lower()

        city_multipliers = {
            "bangalore": 1.10,
            "bengaluru": 1.10,
            "gurgaon": 1.10,
            "gurugram": 1.10,
            "hyderabad": 1.08,
            "mumbai": 1.08,
            "pune": 1.05,
            "noida": 1.00,
            "delhi": 1.00,
            "new delhi": 1.00,
            "chennai": 1.00,
            "kolkata": 0.92,
            "ahmedabad": 0.92,
            "jaipur": 0.90,
            "indore": 0.88,
            "chandigarh": 0.92,
            "kochi": 0.88,
            "coimbatore": 0.86,
        }

        return city_multipliers.get(normalized_city, 0.95)

    def _skill_readiness_multiplier(
        self,
        role_intelligence: Optional[RoleIntelligenceResult]
    ) -> float:

        if not role_intelligence:
            return 1.0

        matched_count = len(role_intelligence.matched_skills or [])
        missing_core_count = len(role_intelligence.missing_core_skills or [])
        missing_growth_count = len(role_intelligence.missing_growth_skills or [])
        high_priority_missing_count = len(
            role_intelligence.high_priority_missing_skills or []
        )

        total_signal_count = (
            matched_count +
            missing_core_count +
            missing_growth_count
        )

        if total_signal_count == 0:
            return 1.0

        readiness_ratio = matched_count / total_signal_count

        if readiness_ratio >= 0.75 and high_priority_missing_count <= 2:
            return 1.08

        if readiness_ratio >= 0.55:
            return 1.0

        if readiness_ratio >= 0.35:
            return 0.95

        return 0.90

    def _calculate_salary_gap(
        self,
        current_salary: float,
        market_min: float,
        market_max: float
    ) -> str:

        if current_salary < market_min:
            min_gap = round(market_min - current_salary, 1)
            max_gap = round(market_max - current_salary, 1)
            return f"+{min_gap}L to +{max_gap}L"

        if current_salary > market_max:
            return "You are above the estimated market range"

        return "You are within the expected market range"

    def _build_confidence_note(
        self,
        salary_band: Dict[str, Any],
        role_intelligence: Optional[RoleIntelligenceResult],
        experience_label: str,
        city: str,
        skill_multiplier: float
    ) -> str:

        role_confidence = "unknown"

        if role_intelligence:
            role_confidence = role_intelligence.confidence

        skill_note = "balanced skill readiness"

        if skill_multiplier > 1.0:
            skill_note = "strong skill readiness"
        elif skill_multiplier < 1.0:
            skill_note = "some important skills still missing"

        return (
            f"Medium - Phase 2 salary estimate based on role cluster, "
            f"experience band ({experience_label}), city factor ({city}), "
            f"role mapping confidence ({role_confidence}), and {skill_note}. "
            f"This is still an estimate, not verified live market data."
        )

    def _fallback_salary(
        self,
        profile: CareerProfileRequest,
        role_cluster: str
    ) -> SalaryInsight:

        salary_bands = {
            "Backend Engineering": [
                (0, 2, 4, 8),
                (2, 5, 8, 18),
                (5, 8, 16, 32),
                (8, 15, 28, 55)
            ],
            "Frontend Engineering": [
                (0, 2, 3.5, 7),
                (2, 5, 7, 16),
                (5, 8, 14, 28),
                (8, 15, 24, 48)
            ],
            "Testing/QA": [
                (0, 2, 2.5, 5),
                (2, 5, 4, 9),
                (5, 8, 7, 14),
                (8, 15, 12, 22)
            ],
            "Application Support": [
                (0, 2, 2.5, 5),
                (2, 5, 5, 10),
                (5, 8, 8, 16),
                (8, 15, 14, 26)
            ],
            "Business Analysis": [
                (0, 2, 3.5, 7),
                (2, 5, 6, 14),
                (5, 8, 11, 25),
                (8, 15, 20, 42)
            ],
            "General IT": [
                (0, 2, 3, 6),
                (2, 5, 5, 12),
                (5, 8, 9, 22),
                (8, 15, 16, 35)
            ]
        }

        bands = salary_bands.get(role_cluster, salary_bands["General IT"])

        selected_band = bands[-1]

        for band in bands:
            min_exp, max_exp, min_lpa, max_lpa = band
            if min_exp <= profile.experience_years < max_exp:
                selected_band = band
                break

        _, _, min_lpa, max_lpa = selected_band

        city_multiplier = self._city_multiplier(profile.city)

        market_min = round(min_lpa * city_multiplier, 1)
        market_max = round(max_lpa * city_multiplier, 1)

        salary_gap = self._calculate_salary_gap(
            profile.current_salary_lpa,
            market_min,
            market_max
        )

        return SalaryInsight(
            current_salary_lpa=profile.current_salary_lpa,
            market_min_lpa=market_min,
            market_max_lpa=market_max,
            salary_gap_lpa=salary_gap,
            confidence="Low/Medium - fallback salary estimate because Salary Engine v2 band was not found."
        )