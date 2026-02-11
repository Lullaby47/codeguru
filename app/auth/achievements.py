"""
Achievement system (F8).
Awards: first_solve, level_5, streak_7, fast_track
Each awarded at most once (UNIQUE user_id+key).
"""
from sqlalchemy.orm import Session
from app.submissions.models import UserAchievement

# Achievement definitions for display
ACHIEVEMENTS = {
    "first_solve":  {"icon": "🏆", "label": "First Solve",          "desc": "Solved your first challenge"},
    "level_5":      {"icon": "⭐", "label": "Level 5 Reached",      "desc": "Reached level 5 in any category"},
    "streak_7":     {"icon": "🔥", "label": "7-Day Streak",         "desc": "Maintained a 7-day streak"},
    "fast_track":   {"icon": "🚀", "label": "Fast Track Activated", "desc": "Activated Fast Track mode"},
}


def _award(db: Session, user_id: int, key: str) -> bool:
    """Try to award an achievement. Returns True if newly awarded, False if already had."""
    existing = db.query(UserAchievement).filter_by(user_id=user_id, key=key).first()
    if existing:
        return False
    db.add(UserAchievement(user_id=user_id, key=key))
    db.commit()
    print(f"[ACHIEVEMENT] user={user_id} earned '{key}'", flush=True)
    return True


def check_first_solve(db: Session, user_id: int):
    """Award after any correct submission."""
    _award(db, user_id, "first_solve")


def check_level_5(db: Session, user_id: int, new_level: int):
    """Award when any category reaches level 5."""
    if new_level >= 5:
        _award(db, user_id, "level_5")


def check_streak_7(db: Session, user_id: int, streak: int):
    """Award when streak reaches 7."""
    if streak >= 7:
        _award(db, user_id, "streak_7")


def check_fast_track(db: Session, user_id: int):
    """Award when fast track is activated."""
    _award(db, user_id, "fast_track")


def get_user_achievements(db: Session, user_id: int) -> list[dict]:
    """Return list of earned achievements with metadata."""
    rows = db.query(UserAchievement).filter_by(user_id=user_id).all()
    earned_keys = {r.key: r.earned_at for r in rows}
    result = []
    for key, meta in ACHIEVEMENTS.items():
        if key in earned_keys:
            result.append({
                "key": key,
                "icon": meta["icon"],
                "label": meta["label"],
                "desc": meta["desc"],
                "earned_at": str(earned_keys[key]) if earned_keys[key] else None,
                "earned": True,
            })
        else:
            result.append({
                "key": key,
                "icon": meta["icon"],
                "label": meta["label"],
                "desc": meta["desc"],
                "earned": False,
            })
    return result
