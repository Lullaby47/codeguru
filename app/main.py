import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.web.debug_routes import router as debug_router

from app.db.base import Base, engine
from app.auth.models import User
from app.auth.category_progress import UserCategoryProgress, DailyAssignment  # Import for table creation
from app.submissions.models import UserAchievement  # Import so create_all picks it up

from app.auth.routes import router as auth_router
from app.web.routes import router as web_router
from app.challenges.routes import router as challenge_router
from app.submissions.routes import router as submission_router
from app.api.routes import router as api_router


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

# Ensure new columns exist on user_category_progress (safe migration for SQLite)
try:
    from sqlalchemy import inspect as _insp, text as _text
    _inspector = _insp(engine)
    if "user_category_progress" in _inspector.get_table_names():
        _cols = [c["name"] for c in _inspector.get_columns("user_category_progress")]
        with engine.connect() as _conn:
            if "solved_current_level_count" not in _cols:
                _conn.execute(_text("ALTER TABLE user_category_progress ADD COLUMN solved_current_level_count INTEGER NOT NULL DEFAULT 0"))
                print("[DB] Added user_category_progress.solved_current_level_count", flush=True)
            if "fast_track_enabled" not in _cols:
                _conn.execute(_text("ALTER TABLE user_category_progress ADD COLUMN fast_track_enabled BOOLEAN NOT NULL DEFAULT 0"))
                print("[DB] Added user_category_progress.fast_track_enabled", flush=True)
            _conn.commit()
except Exception as e:
    print("[DB] user_category_progress migration:", repr(e), flush=True)

# Ensure submissions.actual_output exists (F2 wrong-answer feedback)
try:
    if "submissions" in _inspector.get_table_names():
        _sub_cols = [c["name"] for c in _inspector.get_columns("submissions")]
        if "actual_output" not in _sub_cols:
            with engine.connect() as _conn:
                _conn.execute(_text("ALTER TABLE submissions ADD COLUMN actual_output TEXT"))
                _conn.commit()
            print("[DB] Added submissions.actual_output", flush=True)
except Exception as e:
    print("[DB] submissions migration:", repr(e), flush=True)

# Ensure submission_insights.ai_hint exists (AI hints caching)
try:
    if "submission_insights" in _inspector.get_table_names():
        _si_cols = [c["name"] for c in _inspector.get_columns("submission_insights")]
        if "ai_hint" not in _si_cols:
            with engine.connect() as _conn:
                _conn.execute(_text("ALTER TABLE submission_insights ADD COLUMN ai_hint TEXT"))
                _conn.commit()
            print("[DB] Added submission_insights.ai_hint", flush=True)
except Exception as e:
    print("[DB] submission_insights migration:", repr(e), flush=True)

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

# Ensure users.last_active exists (online status tracking for admin)
try:
    if "users" in _inspector.get_table_names():
        _user_cols = [c["name"] for c in _inspector.get_columns("users")]
        if "last_active" not in _user_cols:
            with engine.connect() as _conn:
                _conn.execute(_text("ALTER TABLE users ADD COLUMN last_active TIMESTAMP"))
                _conn.commit()
            print("[DB] Added users.last_active", flush=True)
except Exception as e:
    print("[DB] users.last_active migration:", repr(e), flush=True)

# Log OpenAI status once at startup (unified client)
try:
    from app.ai.openai_client import log_startup as _ai_log_startup
    _ai_log_startup()
except Exception as _e:
    print(f"[AI] startup log failed: {_e}", flush=True)

# Include routers
app.include_router(auth_router)
app.include_router(web_router)
app.include_router(challenge_router)
app.include_router(submission_router)
app.include_router(api_router)


# Redirect root to login page
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/login")

