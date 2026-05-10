from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.models import (
    CareerProfileRequest,
    CareerAnalysisResponse,
    ResumeOptimizeRequest,
    ResumeOptimizeResponse,
    LearningPlanRequest,
    LearningPlanResponse,
)
from app.services.career_service import CareerService
from app.services.resume_service import ResumeService
from app.services.learning_service import LearningService

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