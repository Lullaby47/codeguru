"""
API routes for user profile and progress.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.models import User
from app.auth.category_level import get_all_user_category_levels_as_list, build_ui_progress_context
from app.auth.achievements import get_user_achievements, check_streak_7
from app.core.deps import get_current_user
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/me/progress")
def get_me_progress(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Return user profile and per-category levels (with progress bar data) for UI display.
    """
    ctx = build_ui_progress_context(db, user.id)
    # F8: check streak achievement opportunistically
    try:
        check_streak_7(db, user.id, user.streak)
    except Exception:
        pass
    achievements = get_user_achievements(db, user.id)
    return {
        "username": user.username,
        "verified": user.is_verified,
        "status": "Active",
        "streak": user.streak,
        "category_levels": ctx["category_levels"],
        "next_goal": ctx["next_goal"],
        "achievements": achievements,
    }
