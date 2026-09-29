from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.models import Arrival, Line, Trip

# 实际到站（自基准起的分钟数）。判定阈值：串车 <3，大间隔 >15，计划 8。
# 起点站：全部正常；市民中心：仅 T01→T02 串车（2 分钟）；
# 火车站：仅 T02→T03 大间隔（16 分钟）；终点站：全部正常。
# 只有市民中心、火车站两个站预览会露出异常。
ARRIVAL_MINUTES = {
    "起点站":  {"T01": 0,  "T02": 8,  "T03": 16, "T04": 24},
    "市民中心": {"T01": 7,  "T02": 9,  "T03": 17, "T04": 25},
    "火车站": {"T01": 12, "T02": 18, "T03": 34, "T04": 42},
    "终点站":  {"T01": 18, "T02": 26, "T03": 36, "T04": 48},
}

STOPS = ["起点站", "市民中心", "火车站", "终点站"]


def seed_if_empty(db: Session) -> None:
    if (db.scalar(select(func.count()).select_from(Line)) or 0) > 0:
        return
    base = datetime(2026, 9, 17, 7, 0, 0)
    line = Line(code="B12", name="城东环线", planned_headway_min=8.0,
                bunch_threshold=3.0, large_threshold=15.0)
    db.add(line)
    db.flush()
    # 计划发车间隔 8 分钟
    specs = [("T01", "粤A1001", 0), ("T02", "粤A1002", 8),
             ("T03", "粤A1003", 16), ("T04", "粤A1004", 24)]
    for trip_no, vehicle, offset in specs:
        trip = Trip(line_id=line.id, trip_no=trip_no,
                    planned_depart=base + timedelta(minutes=offset), vehicle_no=vehicle)
        db.add(trip)
        db.flush()
        for seq, stop in enumerate(STOPS):
            minutes = ARRIVAL_MINUTES[stop][trip_no]
            db.add(Arrival(trip_id=trip.id, stop_name=stop, stop_seq=seq,
                           actual_arrive=base + timedelta(minutes=minutes)))
    db.commit()
