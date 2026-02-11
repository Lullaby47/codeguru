"""
Helper functions for managing per-category user levels.
"""
from sqlalchemy.orm import Session
from app.auth.category_progress import UserCategoryProgress


def get_user_category_level(db: Session, user_id: int, main_category: str, default: int = 1) -> int:
    """
    Get user's level for a specific category.
    Returns default (1) if no progress record exists.
    
    Args:
        db: Database session
        user_id: User ID
        main_category: Category name (normalized)
        default: Default level if no record exists (default: 1)
    
    Returns:
        User's level for this category
    """
    if not main_category or not main_category.strip():
        return default
    
    category_normalized = main_category.strip()
    
    progress = db.query(UserCategoryProgress).filter(
        UserCategoryProgress.user_id == user_id,
        UserCategoryProgress.main_category == category_normalized
    ).first()
    
    if progress:
        return progress.level
    
    return default


def set_user_category_level(db: Session, user_id: int, main_category: str, level: int) -> UserCategoryProgress:
    """
    Set user's level for a specific category.
    Creates record if it doesn't exist, updates if it does.
    
    Args:
        db: Database session
        user_id: User ID
        main_category: Category name (normalized)
        level: New level
    
    Returns:
        UserCategoryProgress record
    """
    if not main_category or not main_category.strip():
        raise ValueError("main_category cannot be empty")
    
    category_normalized = main_category.strip()
    
    progress = db.query(UserCategoryProgress).filter(
        UserCategoryProgress.user_id == user_id,
        UserCategoryProgress.main_category == category_normalized
    ).first()
    
    if progress:
        progress.level = level
    else:
        progress = UserCategoryProgress(
            user_id=user_id,
            main_category=category_normalized,
            level=level
        )
        db.add(progress)
    
    db.commit()
    db.refresh(progress)
    return progress


def increment_user_category_level(db: Session, user_id: int, main_category: str) -> UserCategoryProgress:
    """
    Increment user's level for a specific category by 1.
    
    Args:
        db: Database session
        user_id: User ID
        main_category: Category name (normalized)
    
    Returns:
        Updated UserCategoryProgress record
    """
    current_level = get_user_category_level(db, user_id, main_category)
    return set_user_category_level(db, user_id, main_category, current_level + 1)


def sync_user_category_level(db: Session, user_id: int, main_category: str) -> int:
    """
    Auto-sync user's category level based on how many challenges they've actually solved.
    If the user has solved enough challenges at their current level to level up, 
    this function will increment their level (possibly multiple times).
    
    This fixes cases where challenges were solved before the per-category level system
    was introduced, or where the level-up didn't trigger properly.
    
    Args:
        db: Database session
        user_id: User ID
        main_category: Category name
    
    Returns:
        The (possibly updated) level for this category
    """
    from sqlalchemy import func, distinct
    from app.challenges.models import Challenge
    from app.submissions.models import Submission

    if not main_category or not main_category.strip():
        return 1

    category_normalized = main_category.strip()
    current_level = get_user_category_level(db, user_id, category_normalized)
    leveled_up = False

    # Repeatedly check: if solved_count at current_level >= current_level, level up
    for _ in range(20):  # safety cap to prevent infinite loop
        solved_count = (
            db.query(func.count(distinct(Submission.challenge_id)))
            .join(Challenge, Challenge.id == Submission.challenge_id)
            .filter(
                Submission.user_id == user_id,
                Submission.is_correct == 1,
                Challenge.level == current_level,
                Challenge.main_category == category_normalized,
            )
            .scalar()
        ) or 0

        if solved_count >= current_level:
            progress = increment_user_category_level(db, user_id, category_normalized)
            old = current_level
            current_level = progress.level
            leveled_up = True
            print(f"[SYNC] Auto level-up '{category_normalized}' for user {user_id}: {old} -> {current_level} (solved {solved_count} at level {old})", flush=True)
        else:
            break

    if not leveled_up:
        print(f"[SYNC] No level change needed for '{category_normalized}' user {user_id}: level {current_level} (solved {solved_count}/{current_level})", flush=True)

    return current_level


def get_all_user_category_levels(db: Session, user_id: int) -> dict[str, int]:
    """
    Get all category levels for a user.
    
    Args:
        db: Database session
        user_id: User ID
    
    Returns:
        Dictionary mapping category name to level
    """
    progress_records = db.query(UserCategoryProgress).filter(
        UserCategoryProgress.user_id == user_id
    ).all()
    
    return {record.main_category: record.level for record in progress_records}


def get_all_user_category_levels_as_list(
    db: Session, user_id: int, include_all_categories: bool = True
) -> list[dict]:
    """
    Get all category levels for a user as a list of {main_category, level}.
    If include_all_categories is True, includes ALL categories from challenges table
    (categories user hasn't started get default level 1).
    
    Args:
        db: Database session
        user_id: User ID
        include_all_categories: If True, include categories from DB even if user has no progress
    
    Returns:
        List of {"main_category": str, "level": int} sorted by main_category
    """
    from sqlalchemy import distinct, or_
    from app.challenges.models import Challenge
    
    user_levels = get_all_user_category_levels(db, user_id)
    
    if include_all_categories:
        all_categories = (
            db.query(distinct(Challenge.main_category))
            .filter(
                Challenge.main_category.isnot(None),
                Challenge.main_category != "",
                or_(Challenge.is_active.is_(True), Challenge.is_active.is_(None)),
            )
            .order_by(Challenge.main_category)
            .all()
        )
        category_names = [c[0].strip() for c in all_categories if c[0] and c[0].strip()]
        result = [
            {"main_category": cat, "level": user_levels.get(cat, 1)}
            for cat in category_names
        ]
    else:
        result = [
            {"main_category": cat, "level": lev}
            for cat, lev in user_levels.items()
        ]
        result.sort(key=lambda x: x["main_category"])
    
    return result

