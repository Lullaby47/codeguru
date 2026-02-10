from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from sqlalchemy.sql import func

from app.db.base import Base


class UserJourney(Base):
    """
    Source of truth for user progression.

    This table records:
    - what level a user reached
    - how they reached it
    - when it happened
    - optional future data (badges, notes, analytics)

    IMPORTANT:
    - Not used by app logic yet
    - users.level remains authoritative for now
    """

    __tablename__ = "user_journeys"

    id = Column(Integer, primary_key=True, index=True)

    # User this journey belongs to
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Level reached
    level = Column(Integer, nullable=False)

    # Journey state
    # stable | proving | reinforcing | promoted
    status = Column(String, default="stable", nullable=False)

    # What caused this journey entry
    # daily | force_learning | admin | system
    source = Column(String, default="system", nullable=False)

    # Optional submission reference
    related_submission_id = Column(
        Integer,
        ForeignKey("submissions.id"),
        nullable=True,
    )

    # Flexible future storage:
    # badges, explanations, AI notes, metrics, etc.
    extra_data = Column(Text, default="")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
