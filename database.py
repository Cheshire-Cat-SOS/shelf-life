import os
from datetime import date, datetime, timedelta
from typing import Optional

from sqlmodel import SQLModel, create_engine, Session, select
from sqlalchemy import text

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'inventory.db')
ENGINE_URL = f'sqlite:///{DB_PATH}'

engine = create_engine(
    ENGINE_URL,
    connect_args={'check_same_thread': False},
    echo=False,
)


def init_db():
    from models import Item
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.exec(text('PRAGMA journal_mode=WAL;'))
        session.commit()


def get_db():
    with Session(engine) as session:
        yield session


CATEGORIES = [
    '生鲜蔬菜', '烘焙原料', '酒水饮品', '药品保健',
    '肉禽水产', '调味干货', '日化母婴', '熟食速冻',
]

UNITS = [
    'g', 'ml', '个', '片', '粒', '袋', '斤',
]

STORAGE_LOCATIONS = [
    '冷藏室', '冷冻室', '厨房铝柜', '餐边抽', '入户抽', '药箱', '洗漱柜',
]

CATEGORY_ALIASES = {
    'FRESH_PRODUCE': '生鲜蔬菜',
    'BAKING': '烘焙原料',
    'DRINKS': '酒水饮品',
    'MEDICINE': '药品保健',
    'MEAT_FISH': '肉禽水产',
    'CONDIMENTS': '调味干货',
    'DAILY_BABY': '日化母婴',
    'PREPARED_FROZEN': '熟食速冻',
}

STORAGE_LOCATION_ALIASES = {
    'REFRIGERATOR': '冷藏室',
    'FREEZER': '冷冻室',
    'PANTRY': '厨房铝柜',
    'MEDICINE_CABINET': '药箱',
    'ROOM_TEMP': '餐边抽',
    '阴凉干货柜': '厨房铝柜',
    '家庭小药箱': '药箱',
    '常温': '餐边抽',
}

STATUS_ALIASES = {
    'NORMAL': '正常',
    'EXPIRING': '临期',
    'EXPIRED': '过期',
    'CONSUMED': '用光',
    '已过期': '过期',
    '已用完': '用光',
}

CATEGORY_SHORT_LOCATION = {
    '生鲜蔬菜': {'冷藏室': 2, '餐边抽': 2, '入户抽': 2},
    '烘焙原料': {'冷藏室': 2, '厨房铝柜': 2, '餐边抽': 2, '入户抽': 2},
    '肉禽水产': {'冷冻室': 15, '冷藏室': 2},
    '熟食速冻': {'冷冻室': 2, '冷藏室': 2},
    '调味干货': {'厨房铝柜': 30, '餐边抽': 30, '入户抽': 30},
    '酒水饮品': {'厨房铝柜': 30, '餐边抽': 30, '入户抽': 30},
    '药品保健': {'药箱': 30, '入户抽': 30, '洗漱柜': 30},
    '日化母婴': {'洗漱柜': 30, '入户抽': 30, '餐边抽': 30},
}

CATEGORY_DEFAULT_STORAGE = {
    '生鲜蔬菜': '冷藏室',
    '烘焙原料': '冷藏室',
    '酒水饮品': '厨房铝柜',
    '药品保健': '药箱',
    '肉禽水产': '冷冻室',
    '调味干货': '厨房铝柜',
    '日化母婴': '洗漱柜',
    '熟食速冻': '冷冻室',
}


def compute_actual_expire_date(item) -> date:
    if item.is_opened and item.opened_at and item.pao_days:
        opened_exp = item.opened_at + timedelta(days=item.pao_days)
        if item.expire_date:
            return min(item.expire_date, opened_exp)
        return opened_exp
    return item.expire_date or date.today()


def compute_days_left(item) -> int:
    actual = compute_actual_expire_date(item)
    return (actual - date.today()).days


def calculate_item_status(
    expire_date: date,
    prod_date: date | None = None,
    category: str = "其他",
    is_opened: bool = False,
    pao_days: int | None = None,
    remaining_percent: int = 100,
    current_quantity: float = 1.0,
) -> str:
    from models import ItemStatus

    if remaining_percent is not None and remaining_percent <= 0:
        return ItemStatus.CONSUMED.value
    if current_quantity is not None and current_quantity <= 0:
        return ItemStatus.CONSUMED.value

    today = date.today()
    days_left = (expire_date - today).days
    if days_left < 0:
        return ItemStatus.EXPIRED.value

    threshold = _get_threshold(
        expire_date=expire_date,
        prod_date=prod_date,
        category=category,
        is_opened=is_opened,
        pao_days=pao_days,
    )
    if 0 <= days_left <= threshold:
        return ItemStatus.EXPIRING.value
    return ItemStatus.NORMAL.value


def compute_status(item) -> str:
    return calculate_item_status(
        expire_date=item.expire_date or date.today(),
        prod_date=item.prod_date,
        category=item.category,
        is_opened=bool(item.is_opened),
        pao_days=item.pao_days,
        remaining_percent=item.remaining_percent if item.remaining_percent is not None else 100,
        current_quantity=item.current_quantity if item.current_quantity is not None else 1.0,
    )


def _get_threshold(
    *,
    expire_date: date,
    prod_date: date | None,
    category: str,
    is_opened: bool,
    pao_days: int | None,
) -> int:
    if is_opened and pao_days:
        return min(max(int(pao_days * 0.1), 1), 3)

    if prod_date:
        total_days = max((expire_date - prod_date).days, 1)
    else:
        total_days = max((expire_date - date.today()).days + 1, 1)

    if total_days <= 15:
        ratio, floor_days, cap_days = 0.30, 2, 4
    elif total_days <= 30:
        ratio, floor_days, cap_days = 0.25, 4, 8
    elif total_days <= 45:
        ratio, floor_days, cap_days = 0.25, 7, 12
    elif total_days <= 90:
        ratio, floor_days, cap_days = 0.20, 10, 18
    elif total_days <= 180:
        ratio, floor_days, cap_days = 0.20, 20, 45
    else:
        ratio, floor_days, cap_days = 0.20, 30, 50

    ratio_days = int(total_days * ratio)
    return max(floor_days, min(ratio_days, cap_days))


def refresh_item_status(session: Session, item_id: int):
    from models import Item
    item = session.get(Item, item_id)
    if item:
        item.category = CATEGORY_ALIASES.get(item.category, item.category)
        item.storage_location = STORAGE_LOCATION_ALIASES.get(item.storage_location, item.storage_location)
        item.actual_expire_date = compute_actual_expire_date(item)
        item.status = compute_status(item)
        session.add(item)
        session.commit()
        session.refresh(item)
    return item


def refresh_all_statuses(session: Session):
    from models import Item
    items = session.exec(select(Item)).all()
    for item in items:
        item.category = CATEGORY_ALIASES.get(item.category, item.category)
        item.storage_location = STORAGE_LOCATION_ALIASES.get(item.storage_location, item.storage_location)
        item.status = STATUS_ALIASES.get(item.status, item.status)
        item.actual_expire_date = compute_actual_expire_date(item)
        item.status = compute_status(item)
        session.add(item)
    session.commit()


init_db()
