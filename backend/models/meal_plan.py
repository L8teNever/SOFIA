from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base

class MealPlan(Base):
    __tablename__ = "meal_plans"
    id = Column(Integer, primary_key=True, index=True)
    image_url = Column(String, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    days = relationship("MealPlanDay", back_populates="plan", cascade="all, delete-orphan")

class MealPlanDay(Base):
    __tablename__ = "meal_plan_days"
    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("meal_plans.id"), nullable=False)
    date = Column(String, nullable=False)  # ISO date YYYY-MM-DD
    meal = Column(Text, nullable=False)
    edited = Column(Boolean, default=False)  # true once a human corrected the AI's reading

    plan = relationship("MealPlan", back_populates="days")
