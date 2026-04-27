from pydantic import BaseModel, Field
from typing import List, Dict, Optional


class CareerProfileRequest(BaseModel):
    current_role: str = Field(..., example="Java Developer")
    experience_years: float = Field(..., example=4)
    current_salary_lpa: float = Field(..., example=10)
    city: str = Field(..., example="Bangalore")
    skills: List[str] = Field(..., example=["Java", "Spring Boot", "Microservices"])
    goal: str = Field(..., example="Increase salary")


class SalaryInsight(BaseModel):
    current_salary_lpa: float
    market_min_lpa: float
    market_max_lpa: float
    salary_gap_lpa: Optional[str]
    confidence: str


class CareerAnalysisResponse(BaseModel):
    role_cluster: str
    current_level: str
    salary_insight: SalaryInsight
    target_roles: List[str]
    top_skill_gaps: List[str]
    skill_salary_impact: Dict[str, str]
    roadmap_4_weeks: Dict[str, List[str]]
    resume_suggestions: List[str]
    disclaimer: str