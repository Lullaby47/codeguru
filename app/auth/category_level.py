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
        # F8: check achievements
        try:
            from app.auth.achievements import check_first_solve, check_level_5
            check_first_solve(db, user_id)
            check_level_5(db, user_id, progress.level)
        except Exception:
            pass
        return True, old_level, progress.level

    db.commit()
    # F8: first solve achievement
    try:
        from app.auth.achievements import check_first_solve
        check_first_solve(db, user_id)
    except Exception:
        pass
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
    # F8: award fast track achievement
    try:
        from app.auth.achievements import check_fast_track
        check_fast_track(db, user_id)
    except Exception:
        pass
    return progress


def disable_fast_track(db: Session, user_id: int, main_category: str) -> UserCategoryProgress:
    """Disable fast track for user+category, returning to normal daily mode."""
    progress = get_or_create_progress(db, user_id, main_category)
    progress.fast_track_enabled = False
    db.commit()
    db.refresh(progress)
    print(f"[FAST-TRACK] disabled user={user_id} cat='{main_category}'", flush=True)
    return progress


def toggle_fast_track(db: Session, user_id: int, main_category: str, enabled: bool) -> UserCategoryProgress:
    """Toggle fast track on/off for user+category."""
    if enabled:
        return enable_fast_track(db, user_id, main_category)
    else:
        return disable_fast_track(db, user_id, main_category)


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
# CANONICAL UI FLOW STATE (for consistent challenge rendering)
# ---------------------------------------------------------------------------

def get_challenge_flow_state(db: Session, user_id: int, main_category: str) -> dict:
    """
    Canonical helper for UI challenge flow.
    Returns complete state for rendering /challenge page correctly.
    
    Returns dict with:
        fast_track_enabled: bool
        current_level: int
        required_solves_for_level: int (== current_level)
        solved_current_level_count: int
        daily_assigned_ids_today: list[int]
        daily_completed_today: bool  # True if ALL assigned are solved
        next_unsolved_challenge_id: int|None
        reason: str|None  # e.g. "DAILY_COMPLETE", "NO_QUESTIONS_AT_LEVEL", etc.
        message: str  # User-facing message
    """
    from app.submissions.models import Submission
    
    cat = main_category.strip()
    progress = get_or_create_progress(db, user_id, cat)
    ft = bool(progress.fast_track_enabled)
    level = progress.level
    solved_count = progress.solved_current_level_count
    
    today = date.today()
    
    # Get next challenge selection using existing logic
    selection = get_next_challenge_for_category(db, user_id, cat)
    next_challenge_id = selection.get("challenge_id")
    reason = selection.get("reason")
    message = selection.get("message", "")
    
    # Get daily assignments for today
    daily_assigned = get_daily_assignments(db, user_id, cat, today)
    
    # Check if daily is complete: ALL assigned challenges are solved
    daily_completed_today = False
    if not ft and daily_assigned:
        # Check if ALL assigned challenges have correct submissions
        solved_assigned = set(
            r[0] for r in
            db.query(distinct(Submission.challenge_id))
            .filter(
                Submission.user_id == user_id,
                Submission.is_correct == 1,
                Submission.challenge_id.in_(daily_assigned),
            )
            .all()
        )
        daily_completed_today = len(solved_assigned) == len(daily_assigned)
    
    return {
        "fast_track_enabled": ft,
        "current_level": level,
        "required_solves_for_level": level,  # Need N solves at level N
        "solved_current_level_count": solved_count,
        "daily_assigned_ids_today": daily_assigned,
        "daily_completed_today": daily_completed_today,
        "next_unsolved_challenge_id": next_challenge_id,
        "reason": reason,
        "message": message,
    }


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
    """Returns list of dicts with level + progress bar data per category."""
    from app.challenges.models import Challenge
    from sqlalchemy import distinct as _distinct, or_

    # Fetch all progress records for this user
    all_progress = {
        r.main_category: r
        for r in db.query(UserCategoryProgress)
        .filter(UserCategoryProgress.user_id == user_id).all()
    }

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
    else:
        names = sorted(all_progress.keys())

    result = []
    for n in names:
        prog = all_progress.get(n)
        lvl = prog.level if prog else 1
        solved = prog.solved_current_level_count if prog else 0
        ft = bool(prog.fast_track_enabled) if prog else False
        result.append({
            "main_category": n,
            "level": lvl,
            "solved": solved,
            "required": lvl,          # need lvl solves to advance
            "remaining": max(0, lvl - solved),
            "fast_track": ft,
        })
    return result


# ---------------------------------------------------------------------------
# UI PROGRESS CONTEXT  (single entry point for templates)
# ---------------------------------------------------------------------------

def build_ui_progress_context(
    db: Session, user_id: int, main_category: str | None = None,
) -> dict:
    """
    One-call helper returning everything the UI needs for progress display.
    Returns:
      category_levels: list[dict]  – all categories with level/solved/required/fast_track
      current: dict|None           – detail for selected category (level, solved, required, ft, daily_used)
      next_goal: dict|None         – category closest to leveling up
    """
    cat_levels = get_all_user_category_levels_as_list(db, user_id)

    current = None
    if main_category and main_category.strip():
        cat = main_category.strip()
        prog = get_or_create_progress(db, user_id, cat)
        today = date.today()
        daily_assigned = get_daily_assignments(db, user_id, cat, today)
        daily_solved = count_daily_solved(db, user_id, cat, today)
        current = {
            "main_category": cat,
            "level": prog.level,
            "solved": prog.solved_current_level_count,
            "required": prog.level,
            "remaining": max(0, prog.level - prog.solved_current_level_count),
            "fast_track": bool(prog.fast_track_enabled),
            "daily_used": len(daily_assigned),
            "daily_solved": daily_solved,
            "daily_cap": _DAILY_CAP,
        }

    # Next goal: category with smallest remaining > 0
    next_goal = None
    candidates = [c for c in cat_levels if c["remaining"] > 0]
    if candidates:
        best = min(candidates, key=lambda c: c["remaining"])
        next_goal = {
            "main_category": best["main_category"],
            "level": best["level"],
            "remaining": best["remaining"],
            "next_level": best["level"] + 1,
        }

    return {
        "category_levels": cat_levels,
        "current": current,
        "next_goal": next_goal,
    }
