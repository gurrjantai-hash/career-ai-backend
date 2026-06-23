from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any


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


class TargetSalaryInsight(BaseModel):
    target_role: str
    estimated_min_lpa: float
    estimated_max_lpa: float
    fit_level: str
    salary_upside_note: str


class SkillPremiumInsight(BaseModel):
    skill_name: str
    premium_score: float
    market_relevance: str
    learning_difficulty: str
    proof_required: str
    priority: str
    source: str


class GrowthPath(BaseModel):
    path_name: str
    fit_score: str
    why_it_fits: str
    target_roles: List[str]
    skills_to_build: List[str]


class CareerAnalysisResponse(BaseModel):
    analysis_id: Optional[str] = None
    role_cluster: str
    current_level: str

    summary: str
    recommended_next_move: str
    goal_strategy: str

    salary_insight: SalaryInsight
    target_salary_insights: List[TargetSalaryInsight] = Field(default_factory=list)
    target_roles: List[str]
    top_skill_gaps: List[str]
    skill_salary_impact: Dict[str, str]
    skill_premium_insights: List[SkillPremiumInsight] = Field(default_factory=list)

    growth_paths: List[GrowthPath]
    why_recommendations: List[str]

    roadmap_4_weeks: Dict[str, List[str]]
    resume_suggestions: List[str]

    confidence_notes: List[str]

    disclaimer: str


class ResumeOptimizeRequest(BaseModel):
    current_role: str = Field(..., example="Java Developer")
    experience_years: float = Field(..., example=4)
    city: str = Field(..., example="Bangalore")
    skills: List[str] = Field(..., example=["Java", "Spring Boot", "Microservices"])
    goal: str = Field(..., example="Switch job")
    target_role: str = Field(..., example="Senior Backend Engineer")
    resume_text: str = Field(..., example="Paste resume text here")


class ResumeBulletImprovement(BaseModel):
    original: str
    improved: str
    reason: str


class ResumeOptimizeResponse(BaseModel):
    target_role: str
    resume_alignment: str
    alignment_summary: str
    improved_profile_summary: str
    improved_bullets: List[ResumeBulletImprovement]
    missing_keywords: List[str]
    resume_improvement_priorities: List[str]
    naukri_headline: str
    linkedin_summary: str
    interview_positioning: List[str]
    disclaimer: str


class LearningPlanRequest(BaseModel):
    current_role: str = Field(..., example="Java Developer")
    experience_years: float = Field(..., example=4)
    city: str = Field(..., example="Bangalore")
    skills: List[str] = Field(..., example=["Java", "Spring Boot", "Microservices"])
    goal: str = Field(..., example="Switch job")
    target_role: str = Field(..., example="Senior Backend Engineer")
    skill_gaps: List[str] = Field(
        default=[],
        example=["System Design", "AWS", "Docker"]
    )


class ProjectSuggestion(BaseModel):
    project_name: str
    description: str
    skills_covered: List[str]
    difficulty: str
    portfolio_value: str


class ResourceRecommendation(BaseModel):
    topic: str
    resource_type: str
    what_to_search: str
    expected_outcome: str


class LearningPlanResponse(BaseModel):
    target_role: str
    learning_goal: str
    readiness_level: str
    readiness_summary: str

    revision_topics: List[str]
    new_skills_to_learn: List[str]
    project_suggestions: List[ProjectSuggestion]
    interview_prep_topics: List[str]
    resource_recommendations: List[ResourceRecommendation]
    weekly_learning_plan: Dict[str, List[str]]
    job_readiness_checklist: List[str]

    disclaimer: str


class RoleIntelligenceResult(BaseModel):
    input_role: str
    canonical_role: str
    role_family: str
    primary_cluster: str
    secondary_clusters: List[str]

    matched_skills: List[str]
    missing_core_skills: List[str]
    missing_growth_skills: List[str]
    high_priority_missing_skills: List[str]

    adjacent_paths: List[str]
    confidence: str
    match_score: float

class CareerFeedbackRequest(BaseModel):
    career_analysis_id: Optional[str] = None

    user_current_role: Optional[str] = None
    detected_role_cluster: Optional[str] = None
    user_experience_years: Optional[float] = None
    user_city: Optional[str] = None
    user_goal: Optional[str] = None

    role_mapping_rating: Optional[str] = None
    salary_realism_rating: Optional[str] = None
    target_roles_rating: Optional[str] = None
    skill_recommendations_rating: Optional[str] = None

    overall_rating: Optional[int] = None
    would_pay: Optional[str] = None
    feedback_comment: Optional[str] = None


class CareerFeedbackResponse(BaseModel):
    success: bool
    feedback_id: Optional[int] = None
    message: str    
