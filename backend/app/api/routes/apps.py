from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import AppCreate, AppOut, AppUpdate
from app.services.app_service import create_app, delete_app, get_app, list_apps, update_app

router = APIRouter(prefix="/apps", tags=["apps"])


@router.post("", response_model=AppOut)
def create(payload: AppCreate, db: Session = Depends(get_db)):
    return create_app(db, payload)


@router.get("", response_model=list[AppOut])
def list_all(db: Session = Depends(get_db)):
    return list_apps(db)


@router.get("/{app_id}", response_model=AppOut)
def get_one(app_id: str, db: Session = Depends(get_db)):
    app = get_app(db, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    return app


@router.patch("/{app_id}", response_model=AppOut)
def update(app_id: str, payload: AppUpdate, db: Session = Depends(get_db)):
    app = get_app(db, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    return update_app(db, app, payload)


@router.delete("/{app_id}")
def delete(app_id: str, db: Session = Depends(get_db)):
    app = get_app(db, app_id)
    if not app:
        raise HTTPException(status_code=404, detail="App not found")
    delete_app(db, app)
    return {"ok": True}
