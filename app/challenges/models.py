from sqlalchemy import Column, Integer, String, Text, Date, Boolean, true
from app.db.base import Base


class Challenge(Base):
    __tablename__ = "challenges"

    id = Column(Integer, primary_key=True, index=True)

    # Global pool: only show challenges with is_active=True. Never remove/disable when a user solves.
    is_active = Column(Boolean, nullable=False, default=True, server_default=true())

    level = Column(Integer, nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)

    challenge_date = Column(Date, nullable=True)  # NULL for pool challenges, date for daily challenges

    # Beginner mode expected output
    expected_output = Column(Text, nullable=False, default="")

    # ===============================
    # CATEGORY / TREE STRUCTURE
    # ===============================
    # Allow NULL for backward compatibility & safety
    main_category = Column(
        String(255),
        nullable=True,
        default=""
    )  # e.g. "Basic Python", "Automation"

    sub_category = Column(
        String(255),
        nullable=True,
        default=""
    )  # e.g. "Fundamental", "Intermediate"

    stage_order = Column(
        Integer,
        nullable=False,
        default=1
    )  # e.g. 1, 2, 3...
