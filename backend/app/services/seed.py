from datetime import datetime, timedelta
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models.models import Arrival, Line, Trip

def seed_if_empty(db: Session) -> None:
    if (db.scalar(select(func.count()).select_from(Line)) or 0) > 0:
        return
    base = datetime(2026, 9, 17, 7, 0, 0)
    line = Line(code="B12", name="城东环线", planned_headway_min=8.0, bunch_threshold=3.0, large_threshold=15.0)
    db.add(line); db.flush()
    # 计划每 8 分钟一班；实际偏移在到站层做局部扰动。
    specs = [("T01", "粤A1001", 0), ("T02", "粤A1002", 8), ("T03", "粤A1003", 16), ("T04", "粤A1004", 24)]
    stops = ["起点站", "市民中心", "火车站", "终点站"]
    # 刻意制造：市民中心 T01→T02 间隔 3 分钟（串车）；火车站 T02→T03 间隔 16 分钟（大间隔）。
    # 其余站点相邻间隔均为 8 分钟（正常），两站异常互不污染。
    overrides = {
        ("T02", "市民中心"): timedelta(minutes=8),    # 市民中心：T01@6 → T02@8，间隔 2 分钟（串车）
        ("T03", "火车站"): timedelta(minutes=36),    # 火车站：T02@20 → T03@36，间隔 16 分钟（大间隔）
        ("T04", "火车站"): timedelta(minutes=41),    # T03@36 → T04@41，衔接恢复正常
    }
    for trip_no, vehicle, offset in specs:
        trip = Trip(line_id=line.id, trip_no=trip_no, planned_depart=base + timedelta(minutes=offset), vehicle_no=vehicle)
        db.add(trip); db.flush()
        for seq, stop in enumerate(stops):
            arrive = base + overrides.get((trip_no, stop), timedelta(minutes=offset + seq * 6))
            db.add(Arrival(trip_id=trip.id, stop_name=stop, stop_seq=seq, actual_arrive=arrive))
    db.commit()
