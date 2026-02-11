"""
User Category Progress Model
Tracks per-category level progression for users.
"""
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
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
    xp = Column(Integer, nullable=False, default=0)  # Optional XP tracking
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Unique constraint: one progress record per user per category
    __table_args__ = (
        UniqueConstraint('user_id', 'main_category', name='uq_user_category'),
    )

