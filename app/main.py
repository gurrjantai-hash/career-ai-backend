import os

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.services.embedding_service import EmbeddingService
from app.services.role_intelligence_service import RoleIntelligenceService
from app.services.skill_premium_service import SkillPremiumService
from app.services.feedback_service import FeedbackService

from app.models import (
    CareerProfileRequest,
    CareerAnalysisResponse,
    ResumeOptimizeRequest,
    ResumeOptimizeResponse,
    LearningPlanRequest,
    LearningPlanResponse,
    CareerFeedbackRequest,
    CareerFeedbackResponse,
)

from app.services.career_service import CareerService
from app.services.resume_service import ResumeService
from app.services.learning_service import LearningService


load_dotenv()


app = FastAPI(
    title="Career AI MVP",
    description="AI-powered career and income growth engine",
    version="1.0.0"
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


career_service = CareerService()
resume_service = ResumeService()
learning_service = LearningService()
feedback_service = FeedbackService()

@app.get("/")
def health_check():
    return {"status": "Career AI MVP backend running"}


@app.post("/api/career/analyze", response_model=CareerAnalysisResponse)
def analyze_career(profile: CareerProfileRequest):
    try:
        return career_service.analyze(profile)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/resume/optimize", response_model=ResumeOptimizeResponse)
def optimize_resume(request: ResumeOptimizeRequest):
    try:
        return resume_service.optimize_resume(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/learning/plan", response_model=LearningPlanResponse)
def generate_learning_plan(request: LearningPlanRequest):
    try:
        return learning_service.generate_learning_plan(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/debug/seed-role-embeddings")
def seed_role_embeddings():
    service = EmbeddingService()
    return service.seed_role_title_embeddings()


@app.get("/debug/search-role-embedding")
def search_role_embedding(role: str):
    service = EmbeddingService()
    return {
        "input_role": role,
        "matches": service.find_closest_role(role, limit=5)
    }


@app.post("/debug/role-intelligence")
def debug_role_intelligence(profile: CareerProfileRequest):
    service = RoleIntelligenceService()
    return service.map_role(profile)


@app.post("/debug/skill-premium")
def debug_skill_premium(payload: dict):
    service = SkillPremiumService()

    return {
        "role_cluster": payload.get("role_cluster"),
        "top_skill_gaps": payload.get("top_skill_gaps", []),
        "premium_insights": service.get_skill_premium_insights(
            role_cluster=payload.get("role_cluster"),
            top_skill_gaps=payload.get("top_skill_gaps", []),
            target_roles=payload.get("target_roles", []),
            limit=6
        )
    }


@app.get("/debug/phase-2-health")
def phase_2_health_check():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        return {
            "status": "error",
            "message": "DATABASE_URL is not configured",
            "checks": {}
        }

    try:
        connection = psycopg2.connect(database_url)
        cursor = connection.cursor(cursor_factory=RealDictCursor)

        checks = {}

        checks["canonical_roles_count"] = _get_table_count(
            cursor,
            "canonical_roles"
        )

        checks["role_title_embeddings_count"] = _get_table_count(
            cursor,
            "role_title_embeddings"
        )

        checks["salary_bands_v2_count"] = _get_table_count(
            cursor,
            "salary_bands_v2"
        )

        checks["skill_premium_matrix_count"] = _get_table_count(
            cursor,
            "skill_premium_matrix"
        )

        checks["duplicate_skill_premium_count"] = _get_duplicate_skill_premium_count(
            cursor
        )

        checks["duplicate_role_embedding_count"] = _get_duplicate_role_embedding_count(
            cursor
        )

        checks["required_clusters_in_skill_premium"] = _get_skill_premium_cluster_counts(
            cursor
        )

        cursor.close()
        connection.close()

        warnings = []

        if checks["canonical_roles_count"] == 0:
            warnings.append("canonical_roles table has no data")

        if checks["role_title_embeddings_count"] == 0:
            warnings.append("role_title_embeddings table has no data")

        if checks["salary_bands_v2_count"] == 0:
            warnings.append("salary_bands_v2 table has no data")

        if checks["skill_premium_matrix_count"] == 0:
            warnings.append("skill_premium_matrix table has no data")

        if checks["duplicate_skill_premium_count"] > 0:
            warnings.append("skill_premium_matrix has duplicate skill+cluster rows")

        if checks["duplicate_role_embedding_count"] > 0:
            warnings.append("role_title_embeddings has duplicate title+canonical_role rows")

        missing_skill_clusters = [
            item["cluster"]
            for item in checks["required_clusters_in_skill_premium"]
            if item["row_count"] == 0
        ]

        if missing_skill_clusters:
            warnings.append(
                f"Missing skill premium data for clusters: {', '.join(missing_skill_clusters)}"
            )

        return {
            "status": "ok" if not warnings else "warning",
            "message": "Phase 2 health check completed",
            "warnings": warnings,
            "checks": checks
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "checks": {}
        }


def _get_table_count(cursor, table_name: str) -> int:
    cursor.execute(f"select count(*) as count from {table_name}")
    row = cursor.fetchone()
    return int(row["count"] or 0)


def _get_duplicate_skill_premium_count(cursor) -> int:
    cursor.execute(
        """
        select count(*) as count
        from (
            select
                lower(trim(skill_name)) as skill_name_normalized,
                lower(trim(cluster)) as cluster_normalized,
                count(*) as duplicate_count
            from skill_premium_matrix
            group by lower(trim(skill_name)), lower(trim(cluster))
            having count(*) > 1
        ) duplicates
        """
    )

    row = cursor.fetchone()
    return int(row["count"] or 0)


def _get_duplicate_role_embedding_count(cursor) -> int:
    cursor.execute(
        """
        select count(*) as count
        from (
            select
                lower(trim(title_text)) as title_text_normalized,
                lower(trim(canonical_role)) as canonical_role_normalized,
                count(*) as duplicate_count
            from role_title_embeddings
            group by lower(trim(title_text)), lower(trim(canonical_role))
            having count(*) > 1
        ) duplicates
        """
    )

    row = cursor.fetchone()
    return int(row["count"] or 0)


def _get_skill_premium_cluster_counts(cursor):
    required_clusters = [
        "API Testing",
        "Testing/QA",
        "Automation Testing",
        "SDET",
        "Frontend Engineering",
        "Application Support",
        "Product Analysis",
        "Agile Delivery",
        "Business Analysis",
        "Cloud Support",
        "DevOps",
        "Cloud Engineering",
        "Production Support",
        "Data Analytics",
        "Business Intelligence",
        "Backend Engineering",
        "Full Stack Engineering"
    ]

    cursor.execute(
        """
        select
            cluster,
            count(*) as row_count
        from skill_premium_matrix
        where cluster = any(%s)
        group by cluster
        """,
        (required_clusters,)
    )

    rows = cursor.fetchall()
    existing_counts = {
        row["cluster"]: int(row["row_count"] or 0)
        for row in rows
    }

    return [
        {
            "cluster": cluster,
            "row_count": existing_counts.get(cluster, 0)
        }
        for cluster in required_clusters
    ]

@app.post("/api/feedback", response_model=CareerFeedbackResponse)
def save_feedback(request: CareerFeedbackRequest):
    result = feedback_service.save_feedback(request)

    if not result.success:
        raise HTTPException(status_code=500, detail=result.message)

    return result