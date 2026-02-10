import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Absolute path to project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _build_database_url() -> str:
    """
    Determine the database URL.

    - Prefer DATABASE_URL from the environment (Railway / production).
    - Fallback to a local SQLite file for development.
    - Normalize legacy postgres:// URLs to SQLAlchemy's postgresql+psycopg2://.
    """
    # Prefer explicit DATABASE_URL, fall back to simple local SQLite file.
    # This keeps the default simple and non-hardcoded to a project-specific path.
    url = os.getenv("DATABASE_URL", "sqlite:///./local.db").strip()

    if url.startswith("postgres://"):
        # SQLAlchemy 2.x expects a driver-qualified URL
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)

    return url


DATABASE_URL = _build_database_url()

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # Needed for SQLite when used with FastAPI in a single process
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# Helpful DB diagnostics printed once at startup
try:
    url_safe = engine.url.render_as_string(hide_password=True)
    backend = engine.url.get_backend_name()
    print(f"[DB] Using database backend={backend} url={url_safe}", flush=True)

    if backend == "sqlite":
        db_path = Path(engine.url.database or "").resolve()
        exists = db_path.exists()
        size = db_path.stat().st_size if exists else 0
        print(f"[DB] SQLite path={db_path} exists={exists} size_bytes={size}", flush=True)
except Exception as exc:
    # Never crash app on logging
    print("[DB] Failed to log DB diagnostics:", repr(exc), flush=True)
