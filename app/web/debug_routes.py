from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pathlib import Path

from app.db.session import get_db
from app.auth.models import User
from app.db.base import engine

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/users")
def debug_users(db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id.asc()).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "username": u.username,
            "level": getattr(u, "level", None),
            "role": getattr(u, "role", None),
            "created_at": str(getattr(u, "created_at", "")),
        }
        for u in users
    ]


@router.get("/diagnostics/db")
def db_diagnostics():
    """
    Lightweight DB diagnostics for debugging deployments.

    This endpoint is meant to be exposed only when ENABLE_DEBUG_ROUTES=1.
    It intentionally avoids leaking secrets while still being useful.
    """
    url = engine.url
    backend = url.get_backend_name()
    rendered = url.render_as_string(hide_password=True)

    info = {
        "backend": backend,
        "url": rendered,
    }

    if backend == "sqlite":
        db_path = Path(url.database or "").resolve()
        exists = db_path.exists()
        size = db_path.stat().st_size if exists else 0
        info.update(
            {
                "sqlite_path": str(db_path),
                "sqlite_exists": exists,
                "sqlite_size_bytes": size,
            }
        )
    else:
        info.update(
            {
                "database": url.database,
                "host": url.host,
                "port": url.port,
                "drivername": url.drivername,
            }
        )

    return info
