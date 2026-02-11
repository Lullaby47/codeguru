"""
User Category Progress Model + Daily Assignment Model
Tracks per-category level progression for users.
"""
from sqlalchemy import Column, Integer, String, Date, Boolean, DateTime, UniqueConstraint
from sqlalchemy.sql import func
from app.db.base import Base


class UserCategoryProgress(Base):
    """
    Tracks user progress per main_category.
    Each user has a separate level for each category they've interacted with.
    """
    __tablename__ = "user_category_progress"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, nullable=False, index=True)
    main_category = Column(String(255), nullable=False)

    level = Column(Integer, nullable=False, default=1)
    solved_current_level_count = Column(Integer, nullable=False, default=0)
    fast_track_enabled = Column(Boolean, nullable=False, default=False)
    xp = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Unique constraint: one progress record per user per category
    __table_args__ = (
        UniqueConstraint('user_id', 'main_category', name='uq_user_category'),
    )


class DailyAssignment(Base):
    """
    Persists daily challenge assignments so normal-mode users get max 2/day/category
    without reroll on refresh.
    """
    __tablename__ = "daily_assignments"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, nullable=False, index=True)
    main_category = Column(String(255), nullable=False)
    assignment_date = Column(Date, nullable=False)
    level_at_assignment = Column(Integer, nullable=False)
    challenge_id = Column(Integer, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('user_id', 'main_category', 'assignment_date', 'challenge_id',
                         name='uq_daily_assignment'),
    )

