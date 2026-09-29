from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.models import Arrival

router = APIRouter(prefix="/arrivals", tags=["arrivals"])


class ArrivalPatch(BaseModel):
    actual_arrive: datetime


@router.get("")
def list_arrivals(line_id: int | None = None, db: Session = Depends(get_db)):
    rows = db.scalars(select(Arrival).options(joinedload(Arrival.trip)).order_by(Arrival.actual_arrive)).unique().all()
    out = []
    for r in rows:
        if line_id is not None and r.trip.line_id != line_id: continue
        out.append({"id": r.id, "trip_id": r.trip_id, "trip_no": r.trip.trip_no, "line_id": r.trip.line_id,
                    "stop_name": r.stop_name, "stop_seq": r.stop_seq, "actual_arrive": r.actual_arrive.isoformat()})
    return out


@router.patch("/{arrival_id}")
def update_arrival(arrival_id: int, body: ArrivalPatch, db: Session = Depends(get_db)):
    """修改某条到站记录的实际到站时刻；下次提交按新时刻重算。"""
    row = db.get(Arrival, arrival_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"到站记录 {arrival_id} 不存在")
    row.actual_arrive = body.actual_arrive
    db.commit()
    db.refresh(row)
    return {"id": row.id, "trip_id": row.trip_id, "stop_name": row.stop_name,
            "stop_seq": row.stop_seq, "actual_arrive": row.actual_arrive.isoformat()}
