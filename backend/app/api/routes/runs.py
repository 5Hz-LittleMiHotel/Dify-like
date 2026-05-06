from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import RunOut, RunStepOut
from app.services.app_service import get_app
from app.services.run_log_service import list_run_steps, list_runs

router = APIRouter(tags=["runs"])


@router.get("/apps/{app_id}/runs", response_model=list[RunOut])
def list_app_runs(app_id: str, db: Session = Depends(get_db)):
    if not get_app(db, app_id):
        raise HTTPException(status_code=404, detail="App not found")
    return list_runs(db, app_id)


@router.get("/runs/{run_id}/steps", response_model=list[RunStepOut])
def get_run_steps(run_id: str, db: Session = Depends(get_db)):
    return list_run_steps(db, run_id)
