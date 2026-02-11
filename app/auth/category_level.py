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

