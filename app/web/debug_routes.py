from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.models import User

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
