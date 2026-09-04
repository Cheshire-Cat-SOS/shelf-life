from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field


class ItemStatus(str, Enum):
    NORMAL = "正常"
    EXPIRING = "临期"
    EXPIRED = "过期"
    CONSUMED = "用光"


class ItemBase(SQLModel):
    name: str = Field(..., min_length=1, max_length=200)
    category: str = Field(..., max_length=100)
    storage_location: str = Field(default="常温", max_length=100)
    initial_quantity: float = Field(default=1.0)
    current_quantity: float = Field(default=1.0)
    unit: str = Field(default="g", max_length=20)
    remaining_percent: int = Field(default=100, ge=0, le=100)
    image_url: Optional[str] = Field(default=None, max_length=2000)
    prod_date: Optional[date] = Field(default=None)
    shelf_life_days: Optional[int] = Field(default=None)
    expire_date: Optional[date] = Field(default=None)
    is_opened: bool = Field(default=False)
    opened_at: Optional[date] = Field(default=None)
    pao_days: Optional[int] = Field(default=None)


class Item(ItemBase, table=True):
    __tablename__ = "items"
    id: Optional[int] = Field(default=None, primary_key=True)
    status: str = Field(default=ItemStatus.NORMAL.value, max_length=20)
    actual_expire_date: Optional[date] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)


class ItemCreate(ItemBase):
    actual_expire_date: Optional[date] = None


class ItemRead(ItemBase):
    id: int
    status: str
    actual_expire_date: Optional[date]
    created_at: datetime


class ItemUpdate(SQLModel):
    current_quantity: Optional[float] = None
    remaining_percent: Optional[int] = None
    is_opened: Optional[bool] = None
    opened_at: Optional[date] = None
    pao_days: Optional[int] = None
    status: Optional[str] = None
    actual_expire_date: Optional[date] = None
    name: Optional[str] = None
    category: Optional[str] = None
    storage_location: Optional[str] = None
    initial_quantity: Optional[float] = None
    current_quantity: Optional[float] = None
    unit: Optional[str] = None
    prod_date: Optional[date] = None
    expire_date: Optional[date] = None
