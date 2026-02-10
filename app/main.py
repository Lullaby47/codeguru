from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.db.base import Base, engine
from app.auth.models import User

from app.auth.routes import router as auth_router
from app.web.routes import router as web_router
from app.challenges.routes import router as challenge_router
from app.submissions.routes import router as submission_router  # 🔥 ADD THIS


app = FastAPI(title="CodeGuru", version="0.1.0")

# Create database tables
Base.metadata.create_all(bind=engine)

# Include routers
app.include_router(auth_router)
app.include_router(web_router)
app.include_router(challenge_router)
app.include_router(submission_router)  # 🔥 ADD THIS


# Redirect root to login page
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/login")
