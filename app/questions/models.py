from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base  # Import Base from your base class

class Question(Base):
    __tablename__ = 'questions'

    id = Column(Integer, primary_key=True, index=True)
    level_id = Column(Integer, ForeignKey("levels.id"))
    question_text = Column(String, nullable=False)
    answer = Column(String, nullable=False)

    # Relationship to levels
    level = relationship("Level", back_populates="questions")
