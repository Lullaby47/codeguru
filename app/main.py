import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.web.debug_routes import router as debug_router

from app.db.base import Base, engine
from app.auth.models import User

from app.auth.routes import router as auth_router
from app.web.routes import router as web_router
from app.challenges.routes import router as challenge_router
from app.submissions.routes import router as submission_router  # 🔥 ADD THIS


app = FastAPI(title="CodeGuru", version="0.1.0")

# Mount static files directory
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Only expose debug routes (including diagnostics) when explicitly enabled.
if os.getenv("ENABLE_DEBUG_ROUTES", "0") == "1":
    app.include_router(debug_router)

# Create database tables (still useful in dev; in production prefer Alembic)
Base.metadata.create_all(bind=engine)

# Ensure challenges.is_active exists (per-user pool: never remove questions on solve; filter by is_active only)
try:
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "challenges" in insp.get_table_names():
        cols = [c["name"] for c in insp.get_columns("challenges")]
        if "is_active" not in cols:
            with engine.connect() as conn:
                backend = engine.url.get_backend_name()
                if backend == "sqlite":
                    conn.execute(text("ALTER TABLE challenges ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))
                else:
                    # PostgreSQL; others may need a separate branch
                    conn.execute(text("ALTER TABLE challenges ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT true"))
                conn.commit()
            print("[DB] Added challenges.is_active column for per-user pool", flush=True)
except Exception as e:
    print("[DB] Optional is_active migration:", repr(e), flush=True)

# Include routers
app.include_router(auth_router)
app.include_router(web_router)
app.include_router(challenge_router)
app.include_router(submission_router)  # 🔥 ADD THIS


# Redirect root to login page
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/login")

