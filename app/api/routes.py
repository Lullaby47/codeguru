"""
API routes for user profile and progress.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.category_level import get_all_user_category_levels_as_list
from app.core.deps import get_current_user
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/me/progress")
def get_me_progress(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Return user profile and per-category levels for UI display.
    Replaces deprecated user.level with category_levels.
    """
    category_levels = get_all_user_category_levels_as_list(db, user.id)
    return {
        "username": user.username,
        "verified": user.is_verified,
        "status": "Active",
        "streak": user.streak,
        "category_levels": category_levels,
    }
