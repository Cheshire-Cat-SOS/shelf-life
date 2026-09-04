import base64
import json
import mimetypes
import os
import re
import urllib.error
import urllib.request
from datetime import date
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from starlette.responses import FileResponse

try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    cloudinary = None

from models import Item, ItemCreate, ItemRead, ItemUpdate
from database import (
    compute_actual_expire_date, compute_status,
    get_db, refresh_item_status, refresh_all_statuses,
    CATEGORIES, STORAGE_LOCATIONS, UNITS,
    CATEGORY_ALIASES, STORAGE_LOCATION_ALIASES, STATUS_ALIASES,
)

app = FastAPI(title="保质期管理系统")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

AGNES_API_KEY = os.getenv("AGNES_API_KEY", "sk-4ZqndeBKExFGRjmAsdLdsh5ga1qqHDXAUOwUOWdHvaqKk1bX")
AGNES_API_URL = os.getenv("AGNES_API_URL", "https://apihub.agnes-ai.com/v1/chat/completions")
AGNES_MODEL = os.getenv("AGNES_MODEL", "agnes-2.5-flash")


def normalize_item_fields(item):
    item.category = CATEGORY_ALIASES.get(item.category, item.category)
    item.storage_location = STORAGE_LOCATION_ALIASES.get(item.storage_location, item.storage_location)
    item.status = STATUS_ALIASES.get(item.status, item.status)
    return item


@app.on_event("startup")
async def startup():
    pass


@app.get("/")
async def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(
        os.path.join(STATIC_DIR, "manifest.webmanifest"),
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/api/items", response_model=List[ItemRead])
def list_items(
    category: Optional[str] = None,
    status: Optional[str] = None,
    location: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = select(Item)
    if category:
        query = query.where(Item.category == category)
    if status:
        query = query.where(Item.status == status)
    if location:
        query = query.where(Item.storage_location == location)
    query = query.order_by(Item.status, Item.expire_date)
    items = db.exec(query).all()
    for item in items:
        normalize_item_fields(item)
    return items


@app.post("/api/items", response_model=ItemRead)
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    if not item.expire_date and item.prod_date and item.shelf_life_days:
        item.expire_date = item.prod_date + __import__('datetime').timedelta(days=item.shelf_life_days)
    if not item.expire_date:
        raise HTTPException(status_code=422, detail="请填写到期日期，或同时填写生产日期和保质期天数")
    db_item = Item.model_validate(item)
    normalize_item_fields(db_item)
    db_item.actual_expire_date = compute_actual_expire_date(db_item)
    db_item.status = compute_status(db_item)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


@app.put("/api/items/{item_id}", response_model=ItemRead)
def update_item(item_id: int, item_update: ItemUpdate, db: Session = Depends(get_db)):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    update_data = item_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(item, key, value)
    normalize_item_fields(item)
    refresh_item_status(db, item_id)
    db.refresh(item)
    return item


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    db.delete(item)
    db.commit()
    return {"message": "删除成功"}


@app.post("/api/items/{item_id}/refresh-status")
def api_refresh_status(item_id: int, db: Session = Depends(get_db)):
    item = refresh_item_status(db, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    return item


@app.post("/api/refresh-all-statuses")
def api_refresh_all(db: Session = Depends(get_db)):
    refresh_all_statuses(db)
    return {"message": "所有状态已刷新"}


@app.get("/api/config")
def get_config():
    return {
        "categories": CATEGORIES,
        "storage_locations": STORAGE_LOCATIONS,
        "units": UNITS,
    }


async def save_uploaded_image(file: UploadFile, prefix: str = "image") -> str:
    content = await file.read()
    if cloudinary and os.getenv("CLOUDINARY_URL"):
        result = cloudinary.uploader.upload(
            content,
            folder="shelf-life",
            public_id=f"{prefix}_{os.urandom(8).hex()}",
            resource_type="image",
        )
        return result["secure_url"]

    images_dir = os.path.join(STATIC_DIR, "images")
    os.makedirs(images_dir, exist_ok=True)
    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    filename = f"{prefix}_{os.urandom(4).hex()}{ext}"
    filepath = os.path.join(images_dir, filename)
    with open(filepath, "wb") as f:
        f.write(content)
    return f"/static/images/{filename}"


@app.post("/api/upload-image")
async def upload_image(file: UploadFile = File(...)):
    return {"image_url": await save_uploaded_image(file, "upload")}


@app.post("/api/items/upload-image/{item_id}")
async def upload_item_image(
    item_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="物品不存在")
    image_url = await save_uploaded_image(file, f"item_{item_id}")
    item.image_url = image_url
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"image_url": image_url}


def _extract_json_object(text: str) -> dict:
    if not text:
        return {}
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def _normalize_recognized_value(data: dict) -> dict:
    category = CATEGORY_ALIASES.get(data.get("category"), data.get("category") or "")
    location_raw = data.get("storage_location") or data.get("location")
    location = STORAGE_LOCATION_ALIASES.get(location_raw, location_raw or "")
    unit = data.get("unit") or "g"
    quantity = data.get("current_quantity") or data.get("initial_quantity") or data.get("weight")
    try:
        quantity = float(quantity) if quantity not in (None, "") else None
    except (TypeError, ValueError):
        quantity = None

    return {
        "name": data.get("name") or "",
        "category": category if category in CATEGORIES else "",
        "storage_location": location if location in STORAGE_LOCATIONS else "",
        "initial_quantity": quantity,
        "current_quantity": quantity,
        "unit": unit,
        "prod_date": data.get("prod_date") or data.get("production_date") or "",
        "expire_date": data.get("expire_date") or data.get("expiry") or "",
        "shelf_life_days": data.get("shelf_life_days"),
        "is_opened": bool(data.get("is_opened", False)),
        "opened_at": data.get("opened_at") or "",
        "pao_days": data.get("pao_days"),
    }


@app.post("/api/recognize")
async def recognize_item_image(image: UploadFile = File(...)):
    content = await image.read()
    if cloudinary and os.getenv("CLOUDINARY_URL"):
        upload_result = cloudinary.uploader.upload(
            content,
            folder="shelf-life",
            public_id=f"photo_{os.urandom(8).hex()}",
            resource_type="image",
        )
        image_url = upload_result["secure_url"]
    else:
        images_dir = os.path.join(STATIC_DIR, "images")
        os.makedirs(images_dir, exist_ok=True)
        ext = os.path.splitext(image.filename or "")[1] or ".jpg"
        filename = f"photo_{os.urandom(4).hex()}{ext}"
        filepath = os.path.join(images_dir, filename)
        with open(filepath, "wb") as f:
            f.write(content)
        image_url = f"/static/images/{filename}"
    mime_type = image.content_type or mimetypes.guess_type(filename)[0] or "image/jpeg"
    data_url = f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"
    prompt = (
        "你是家庭库存入库助手。请识别图片中的物品或包装信息，只返回 JSON，不要解释。"
        "字段：name, category, storage_location, initial_quantity, current_quantity, unit, "
        "prod_date, expire_date, shelf_life_days, is_opened, opened_at, pao_days。"
        "category 只能从：生鲜蔬菜、烘焙原料、酒水饮品、药品保健、肉禽水产、调味干货、日化母婴、熟食速冻 中选。"
        "storage_location 只能从：冷藏室、冷冻室、厨房铝柜、餐边抽、入户抽、药箱、洗漱柜 中选。"
        "日期格式用 YYYY-MM-DD；识别不到就用空字符串或 null。"
    )
    payload = {
        "model": AGNES_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 700,
    }
    request = urllib.request.Request(
        AGNES_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {AGNES_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=502, detail=f"AI识别失败：{detail[:200]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI识别失败：{exc}")

    message = result.get("choices", [{}])[0].get("message", {})
    content_text = message.get("content", "")
    if isinstance(content_text, list):
        content_text = "".join(part.get("text", "") for part in content_text if isinstance(part, dict))
    recognized = _normalize_recognized_value(_extract_json_object(content_text))
    recognized["image_url"] = image_url
    return recognized


@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    items = db.exec(select(Item)).all()
    def _status_text(value):
        text = str(value)
        return {
            "NORMAL": "正常",
            "EXPIRING": "临期",
            "EXPIRED": "过期",
            "CONSUMED": "用光",
            "ItemStatus.NORMAL": "正常",
            "ItemStatus.EXPIRING": "临期",
            "ItemStatus.EXPIRED": "过期",
            "ItemStatus.CONSUMED": "用光",
        }.get(text, text)

    stats = {
        "total": len(items),
        "normal": sum(1 for i in items if _status_text(i.status) == "正常"),
        "expiring": sum(1 for i in items if _status_text(i.status) == "临期"),
        "expired": sum(1 for i in items if _status_text(i.status) == "过期"),
        "consumed": sum(1 for i in items if _status_text(i.status) == "用光"),
    }
    return stats


