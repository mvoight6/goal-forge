"""Categories API — CRUD for global categories used by goals and ideas."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from goalforge.capture import _auth
from goalforge import database as db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["categories"])


class CategoryCreate(BaseModel):
    name: str
    icon: str = "🏷️"


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None


class CategoryOrderUpdate(BaseModel):
    ids: list[int]


@router.get("/categories")
def list_categories(_=Depends(_auth)):
    return db.get_categories()


@router.post("/categories", status_code=201)
def create_category(body: CategoryCreate, _=Depends(_auth)):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Category name cannot be empty")
    cat = db.create_category(name, body.icon)
    if cat is None:
        raise HTTPException(409, "Category name already exists")
    return cat


@router.put("/categories/order")
def set_order(body: CategoryOrderUpdate, _=Depends(_auth)):
    db.set_category_order(body.ids)
    return {"ok": True}


@router.put("/categories/{cat_id}")
def update_category(cat_id: int, body: CategoryUpdate, _=Depends(_auth)):
    cat = db.get_category(cat_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    name = body.name.strip() if body.name is not None else None
    if name == "":
        raise HTTPException(400, "Category name cannot be empty")
    db.update_category(cat_id, name, body.icon)
    return db.get_category(cat_id)


@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, _=Depends(_auth)):
    cat = db.get_category(cat_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    db.delete_category(cat_id)
    return {"ok": True}
