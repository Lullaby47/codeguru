"""
Helper functions for managing per-category user levels.
Core rules:
  - Strict level matching: challenge.level == user's category level (no buffer)
  - Level N -> N+1 requires solving N questions at level N
  - solved_current_level_count tracks progress, resets on level-up
  - Daily mode: max 2 challenges/day/category, stable assignment
  - Fast track: no daily cap, immediate next challenge
"""
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from app.auth.category_progress import UserCategoryProgress, DailyAssignment


# ---------------------------------------------------------------------------
# GET / SET / INCREMENT helpers
# ---------------------------------------------------------------------------

def get_or_create_progress(db: Session, user_id: int, main_category: str) -> UserCategoryProgress:
    """Get existing progress record or create one with defaults."""
    cat = main_category.strip()
    progress = db.query(UserCategoryProgress).filter(
        UserCategoryProgress.user_id == user_id,
        UserCategoryProgress.main_category == cat,
    ).first()
    if not progress:
        progress = UserCategoryProgress(
            user_id=user_id,
            main_category=cat,
            level=1,
            solved_current_level_count=0,
            fast_track_enabled=False,
        )
        db.add(progress)
        db.commit()
        db.refresh(progress)
    return progress


def get_user_category_level(db: Session, user_id: int, main_category: str, default: int = 1) -> int:
    """Get user's level for a category. Returns *default* if no record exists."""
    if not main_category or not main_category.strip():
        return default
    progress = db.query(UserCategoryProgress).filter(
        UserCategoryProgress.user_id == user_id,
        UserCategoryProgress.main_category == main_category.strip(),
    ).first()
    return progress.level if progress else default


def set_user_category_level(db: Session, user_id: int, main_category: str, level: int) -> UserCategoryProgress:
    """Set user's level for a category (creates record if needed)."""
    progress = get_or_create_progress(db, user_id, main_category)
    progress.level = level
    db.commit()
    db.refresh(progress)
    return progress


def increment_user_category_level(db: Session, user_id: int, main_category: str) -> UserCategoryProgress:
    """Increment level by 1 and reset solved counter."""
    progress = get_or_create_progress(db, user_id, main_category)
    old = progress.level
    progress.level = old + 1
    progress.solved_current_level_count = 0
    db.commit()
    db.refresh(progress)
    print(f"[LEVEL-UP] user={user_id} cat='{main_category}' {old} -> {progress.level}", flush=True)
    return progress


# ---------------------------------------------------------------------------
# SYNC (back-fill for legacy data)
# ---------------------------------------------------------------------------

def sync_user_category_level(db: Session, user_id: int, main_category: str) -> int:
    """
    One-time back-fill: if the stored solved_current_level_count is stale
    (e.g. challenges solved before the counter existed), recompute from DB
    and apply any pending level-ups.  Returns current level.
    """
    from app.challenges.models import Challenge
    from app.submissions.models import Submission

    if not main_category or not main_category.strip():
        return 1

    cat = main_category.strip()
    progress = get_or_create_progress(db, user_id, cat)

    for _ in range(20):  # safety cap
        solved = (
            db.query(func.count(distinct(Submission.challenge_id)))
            .join(Challenge, Challenge.id == Submission.challenge_id)
            .filter(
                Submission.user_id == user_id,
                Submission.is_correct == 1,
                Challenge.level == progress.level,       # strict equality
                Challenge.main_category == cat,
            )
            .scalar()
        ) or 0

        if solved >= progress.level:
            old = progress.level
            progress.level += 1
            progress.solved_current_level_count = 0
            db.commit()
            print(f"[SYNC] user={user_id} cat='{cat}' {old} -> {progress.level} (solved {solved} at level {old})", flush=True)
        else:
            progress.solved_current_level_count = solved
            db.commit()
            break

    return progress.level


# ---------------------------------------------------------------------------
# LEVEL-UP on correct submission  (Rule C)
# ---------------------------------------------------------------------------

def record_solve_and_maybe_level_up(
    db: Session, user_id: int, main_category: str, challenge_level: int
) -> tuple[bool, int, int]:
    """
    Call after a correct submission.
    Returns (leveled_up, old_level, new_level).
    Only increments counter when challenge_level == user's current level.
    """
    progress = get_or_create_progress(db, user_id, main_category)
    old_level = progress.level

    if challenge_level != progress.level:
        # Challenge is not at user's current level — no counter change
        return False, old_level, old_level

    progress.solved_current_level_count += 1
    print(f"[SOLVE] user={user_id} cat='{main_category}' level={progress.level} "
          f"count={progress.solved_current_level_count}/{progress.level}", flush=True)

    if progress.solved_current_level_count >= progress.level:
        progress.level += 1
        progress.solved_current_level_count = 0
        db.commit()
        db.refresh(progress)
        print(f"[LEVEL-UP] user={user_id} cat='{main_category}' {old_level} -> {progress.level}", flush=True)
        return True, old_level, progress.level

    db.commit()
    return False, old_level, old_level


# ---------------------------------------------------------------------------
# FAST TRACK  (Rule E)
# ---------------------------------------------------------------------------

def is_fast_track(db: Session, user_id: int, main_category: str) -> bool:
    progress = get_or_create_progress(db, user_id, main_category)
    return bool(progress.fast_track_enabled)


def enable_fast_track(db: Session, user_id: int, main_category: str) -> UserCategoryProgress:
    progress = get_or_create_progress(db, user_id, main_category)
    progress.fast_track_enabled = True
    db.commit()
    db.refresh(progress)
    print(f"[FAST-TRACK] enabled user={user_id} cat='{main_category}'", flush=True)
    return progress


# ---------------------------------------------------------------------------
# DAILY ASSIGNMENT  (Rule D)
# ---------------------------------------------------------------------------

_DAILY_CAP = 2  # max challenges per day per category in normal mode


def get_daily_assignments(
    db: Session, user_id: int, main_category: str, today: date | None = None
) -> list[int]:
    """Return today's assigned challenge_ids for this user+category (may be empty)."""
    today = today or date.today()
    rows = (
        db.query(DailyAssignment.challenge_id)
        .filter(
            DailyAssignment.user_id == user_id,
            DailyAssignment.main_category == main_category.strip(),
            DailyAssignment.assignment_date == today,
        )
        .all()
    )
    return [r[0] for r in rows]


def create_daily_assignments(
    db: Session, user_id: int, main_category: str,
    current_level: int, unsolved_ids: list[int],
    today: date | None = None,
) -> list[int]:
    """Pick up to _DAILY_CAP from *unsolved_ids*, persist, return assigned ids."""
    import random
    today = today or date.today()
    cat = main_category.strip()

    # Already assigned today?
    existing = get_daily_assignments(db, user_id, cat, today)
    if existing:
        return existing

    # Pick up to 2 random unsolved
    chosen = random.sample(unsolved_ids, min(_DAILY_CAP, len(unsolved_ids))) if unsolved_ids else []

    for cid in chosen:
        da = DailyAssignment(
            user_id=user_id,
            main_category=cat,
            assignment_date=today,
            level_at_assignment=current_level,
            challenge_id=cid,
        )
        db.add(da)

    if chosen:
        db.commit()

    print(f"[DAILY] user={user_id} cat='{cat}' level={current_level} assigned={chosen} "
          f"unsolved_pool={len(unsolved_ids)}", flush=True)
    return chosen


def count_daily_solved(db: Session, user_id: int, main_category: str, today: date | None = None) -> int:
    """How many of today's assigned challenges has the user already solved?"""
    from app.submissions.models import Submission
    today = today or date.today()
    assigned = get_daily_assignments(db, user_id, main_category, today)
    if not assigned:
        return 0
    solved = (
        db.query(func.count(distinct(Submission.challenge_id)))
        .filter(
            Submission.user_id == user_id,
            Submission.is_correct == 1,
            Submission.challenge_id.in_(assigned),
        )
        .scalar()
    ) or 0
    return solved


# ---------------------------------------------------------------------------
# CHALLENGE SELECTION  (Rules B, D, E)
# ---------------------------------------------------------------------------

def get_next_challenge_for_category(
    db: Session, user_id: int, main_category: str
) -> dict:
    """
    Main entry point: returns the next challenge for user+category.
    Respects strict level, daily cap (normal) / unlimited (fast-track).

    Returns dict:
      {"challenge_id": int|None, "reason": str, "message": str,
       "level": int, "fast_track": bool,
       "daily_assigned": list[int], "daily_solved": int, "daily_cap": int}
    """
    from app.challenges.models import Challenge
    from app.submissions.models import Submission
    from sqlalchemy import or_

    cat = main_category.strip()
    progress = get_or_create_progress(db, user_id, cat)
    level = progress.level
    ft = bool(progress.fast_track_enabled)

    base = {
        "level": level,
        "fast_track": ft,
        "daily_cap": _DAILY_CAP,
    }

    # ── All active challenges at STRICT level for this category ──────────
    act = or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None))
    pool = (
        db.query(Challenge.id)
        .filter(
            Challenge.main_category == cat,
            Challenge.level == level,  # STRICT
            act,
        )
        .all()
    )
    pool_ids = [r[0] for r in pool]

    # ── Already solved by this user ──────────────────────────────────────
    solved_ids = set(
        r[0] for r in
        db.query(distinct(Submission.challenge_id))
        .filter(
            Submission.user_id == user_id,
            Submission.is_correct == 1,
            Submission.challenge_id.in_(pool_ids) if pool_ids else Submission.challenge_id == -1,
        )
        .all()
    )
    unsolved_ids = [cid for cid in pool_ids if cid not in solved_ids]

    print(f"[SELECT] user={user_id} cat='{cat}' level={level} ft={ft} "
          f"pool={len(pool_ids)} solved={len(solved_ids)} unsolved={len(unsolved_ids)}", flush=True)

    # ── No challenges at this level at all ───────────────────────────────
    if not pool_ids:
        return {**base, "challenge_id": None,
                "reason": "NO_QUESTIONS_AT_LEVEL",
                "message": "Wait for Admin/Owner to add more questions.",
                "daily_assigned": [], "daily_solved": 0}

    # ── All solved ───────────────────────────────────────────────────────
    if not unsolved_ids:
        return {**base, "challenge_id": None,
                "reason": "ALL_SOLVED_AT_LEVEL",
                "message": "You've solved all available questions at this level. Wait for Admin/Owner to add more.",
                "daily_assigned": list(solved_ids), "daily_solved": len(solved_ids)}

    # ── FAST TRACK: serve immediately, no daily cap ──────────────────────
    if ft:
        import random
        chosen = random.choice(unsolved_ids)
        return {**base, "challenge_id": chosen,
                "reason": "FAST_TRACK",
                "message": "Fast Track active",
                "daily_assigned": [], "daily_solved": 0}

    # ── NORMAL MODE: respect daily cap of 2 ──────────────────────────────
    today = date.today()
    assigned = create_daily_assignments(db, user_id, cat, level, unsolved_ids, today)
    daily_solved = count_daily_solved(db, user_id, cat, today)

    # Find first unsolved assignment for today
    for cid in assigned:
        if cid not in solved_ids:
            return {**base, "challenge_id": cid,
                    "reason": "DAILY_ASSIGNMENT",
                    "message": f"Daily challenge {daily_solved+1}/{len(assigned)}",
                    "daily_assigned": assigned, "daily_solved": daily_solved}

    # All of today's assignments already solved
    return {**base, "challenge_id": None,
            "reason": "DAILY_CAP_REACHED",
            "message": "You've completed today's challenges. Come back tomorrow or enable Fast Track!",
            "daily_assigned": assigned, "daily_solved": daily_solved}


# ---------------------------------------------------------------------------
# AGGREGATE helpers (for dashboard / profile)
# ---------------------------------------------------------------------------

def get_all_user_category_levels(db: Session, user_id: int) -> dict[str, int]:
    records = db.query(UserCategoryProgress).filter(
        UserCategoryProgress.user_id == user_id
    ).all()
    return {r.main_category: r.level for r in records}


def get_all_user_category_levels_as_list(
    db: Session, user_id: int, include_all_categories: bool = True
) -> list[dict]:
    from app.challenges.models import Challenge
    from sqlalchemy import distinct as _distinct, or_

    user_levels = get_all_user_category_levels(db, user_id)

    if include_all_categories:
        all_cats = (
            db.query(_distinct(Challenge.main_category))
            .filter(
                Challenge.main_category.isnot(None),
                Challenge.main_category != "",
                or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
            )
            .order_by(Challenge.main_category)
            .all()
        )
        names = [c[0].strip() for c in all_cats if c[0] and c[0].strip()]
        return [{"main_category": n, "level": user_levels.get(n, 1)} for n in names]
    else:
        result = [{"main_category": c, "level": l} for c, l in user_levels.items()]
        result.sort(key=lambda x: x["main_category"])
        return result
