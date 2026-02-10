from sqlalchemy import Column, Integer, String
from app.db.base import Base  # Import Base from your base class
from sqlalchemy.orm import relationship

class Level(Base):
    __tablename__ = "levels"

    id = Column(Integer, primary_key=True, index=True)
    level_name = Column(String, nullable=False)

    # Relationship to questions
    questions = relationship("Question", back_populates="level")
