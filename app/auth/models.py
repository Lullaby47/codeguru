
from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
import uuid

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)

    password_hash = Column(String, nullable=False)

    is_verified = Column(Boolean, default=False)
    verification_token = Column(
        String,
        unique=True,
        index=True,
        default=lambda: str(uuid.uuid4())
    )

    level = Column(Integer, default=1)  # DEPRECATED: Use user_category_progress for per-category levels
    streak = Column(Integer, default=0)

    # User role: "user" (default), "coadmin", "admin"
    role = Column(String, default="user", nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Track when user was last active (updated on every authenticated request)
    last_active = Column(DateTime(timezone=True), nullable=True)
