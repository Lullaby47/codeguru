from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from app.db.base import Base


class Submission(Base):
    __tablename__ = "submissions"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    challenge_id = Column(Integer, ForeignKey("challenges.id"), nullable=False)

    code = Column(Text, nullable=False)

    # 1 = correct, 0 = wrong
    is_correct = Column(Integer, default=0)

    # 🔥 FIRST EVER SUBMISSION (WELCOME EVENT)
    is_first_submission = Column(Integer, default=0)

    # 🔁 ATTEMPT TRACKING
    attempt_number = Column(Integer, default=1)
    is_retry = Column(Integer, default=0)

    # Captured output for wrong-answer feedback (F2)
    actual_output = Column(Text, nullable=True, default=None)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ======================================================
# 🧠 SUBMISSION INSIGHTS (CORE LEARNING RECORD)
# ======================================================
class SubmissionInsight(Base):
    __tablename__ = "submission_insights"

    id = Column(Integer, primary_key=True, index=True)

    submission_id = Column(
        Integer,
        ForeignKey("submissions.id"),
        nullable=False,
        unique=True,   # 🔑 one insight per submission
    )

    # 🔍 Concepts detected in the code
    # Example: ["print", "strings", "loops"]
    concepts = Column(Text, default="")

    # 🧠 AI / system generated learning points
    # Stored as plain text (one per line)
    learning_points = Column(Text, default="")

    # 🌍 Real-world relevance explanation
    real_world_use = Column(Text, default="")

    # 🪜 Improvement suggestion
    improvement_hint = Column(Text, default="")

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ======================================================
# 🏆 USER ACHIEVEMENTS (F8)
# ======================================================
class UserAchievement(Base):
    __tablename__ = "user_achievements"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    key = Column(String(64), nullable=False)  # e.g. "first_solve", "level_5", "streak_7", "fast_track"
    earned_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_user_achievement"),
    )
