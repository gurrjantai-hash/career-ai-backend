from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.models import CareerProfileRequest, CareerAnalysisResponse
from app.services.career_service import CareerService

allow_origins=["*"]
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
    allow_headers=["*"],
)

career_service = CareerService()


@app.get("/")
def health_check():
    return {"status": "Career AI MVP backend running"}


@app.post("/api/career/analyze", response_model=CareerAnalysisResponse)
def analyze_career(profile: CareerProfileRequest):
    try:
        return career_service.analyze(profile)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))