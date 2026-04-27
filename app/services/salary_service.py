from app.models import CareerProfileRequest, SalaryInsight


class SalaryService:

    def calculate_salary(self, profile: CareerProfileRequest, role_cluster: str) -> SalaryInsight:
        exp = profile.experience_years
        current = profile.current_salary_lpa

        # MVP salary bands. Later we replace this with ML model.
        salary_bands = {
            "Backend Engineering": [
                (0, 2, 4, 9),
                (2, 5, 8, 18),
                (5, 8, 16, 32),
                (8, 15, 28, 55)
            ],
            "Frontend Engineering": [
                (0, 2, 4, 8),
                (2, 5, 7, 16),
                (5, 8, 14, 28),
                (8, 15, 25, 45)
            ],
            "Data/AI": [
                (0, 2, 5, 12),
                (2, 5, 10, 25),
                (5, 8, 20, 45),
                (8, 15, 35, 70)
            ],
            "Marketing": [
                (0, 2, 3, 7),
                (2, 5, 6, 14),
                (5, 8, 12, 25),
                (8, 15, 22, 45)
            ],
            "Sales": [
                (0, 2, 3, 8),
                (2, 5, 6, 16),
                (5, 8, 14, 30),
                (8, 15, 25, 60)
            ],
            "Finance": [
                (0, 2, 4, 9),
                (2, 5, 7, 18),
                (5, 8, 15, 32),
                (8, 15, 28, 55)
            ],
            "Operations": [
                (0, 2, 3, 7),
                (2, 5, 6, 14),
                (5, 8, 12, 25),
                (8, 15, 22, 40)
            ]
        }

        bands = salary_bands.get(role_cluster, salary_bands["Operations"])

        market_min = 5
        market_max = 12

        for min_exp, max_exp, min_lpa, max_lpa in bands:
            if min_exp <= exp < max_exp:
                market_min = min_lpa
                market_max = max_lpa
                break

        # Basic city adjustment
        city = profile.city.lower()
        if city in ["bangalore", "bengaluru", "hyderabad", "pune", "gurgaon", "gurugram"]:
            market_min *= 1.1
            market_max *= 1.1

        if current < market_min:
            gap = f"+{round(market_min - current, 1)}L to +{round(market_max - current, 1)}L"
        elif current <= market_max:
            gap = "You are within the expected market range"
        else:
            gap = "You are above the estimated market range"

        return SalaryInsight(
            current_salary_lpa=current,
            market_min_lpa=round(market_min, 1),
            market_max_lpa=round(market_max, 1),
            salary_gap_lpa=gap,
            confidence="Medium - MVP estimate based on role cluster, experience and city"
        )