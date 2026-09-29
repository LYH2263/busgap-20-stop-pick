import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Arrival, BunchReport, Line, Trip
from app.services.bunch_engine import detect_bunching, events_to_dicts

router = APIRouter(prefix="/reports", tags=["reports"])


class CommitIn(BaseModel):
    line_id: int
    stop_names: list[str]


def _line_or_404(db: Session, line_id: int) -> Line:
    line = db.get(Line, line_id)
    if not line:
        raise HTTPException(status_code=404, detail=f"非法线路编号：线路 {line_id} 不存在，提交失败。")
    return line


def _trips_and_arrivals(db: Session, line_id: int, stop_name: str | None = None):
    """实时读取一条线路当前的班次与到站（提交重算专用，绝不使用任何缓存数字）。"""
    trips = db.scalars(select(Trip).where(Trip.line_id == line_id).order_by(Trip.id)).all()
    trip_no_map = {t.id: t.trip_no for t in trips}
    if not trips:
        return [], trips
    q = select(Arrival).where(Arrival.trip_id.in_(list(trip_no_map)))
    if stop_name is not None:
        q = q.where(Arrival.stop_name == stop_name)
    arrivals = db.scalars(q).all()
    payload = [
        {"stop_name": a.stop_name, "trip_no": trip_no_map[a.trip_id], "actual_arrive": a.actual_arrive}
        for a in arrivals
    ]
    return payload, trips


def _stop_order(db: Session, trip_ids: list[int]) -> list[str]:
    rows = db.execute(
        select(Arrival.stop_name)
        .where(Arrival.trip_id.in_(trip_ids))
        .group_by(Arrival.stop_name)
        .order_by(func.min(Arrival.stop_seq))
    ).all()
    return [r[0] for r in rows]


def _severity(event, line: Line) -> float | None:
    """异常相对阈值的偏离幅度；正常事件不参与“最严重”评选。"""
    if event.status == "bunching":
        return line.bunch_threshold - event.gap_min
    if event.status == "large_gap":
        return event.gap_min - line.large_threshold
    return None


@router.get("")
def list_reports(db: Session = Depends(get_db)):
    """已落库的单站报告（报告页唯一数据来源）。"""
    rows = db.scalars(select(BunchReport).order_by(BunchReport.id.desc())).all()
    return [
        {"id": r.id, "line_id": r.line_id, "stop_name": r.stop_name,
         "created_at": r.created_at.isoformat(), "events": json.loads(r.summary_json)}
        for r in rows
    ]


@router.get("/preview")
def preview(line_id: int, db: Session = Depends(get_db)):
    """只读预览：按站给出串车条数、大间隔条数、最严重一条的间隔与状态。不落库、不产生报告。"""
    line = _line_or_404(db, line_id)
    payload, trips = _trips_and_arrivals(db, line_id)
    events = detect_bunching(payload, line.planned_headway_min, line.bunch_threshold, line.large_threshold)
    event_dicts = events_to_dicts(events)

    by_stop: dict[str, list[dict]] = {}
    for ev, ev_dict in zip(events, event_dicts):
        by_stop.setdefault(ev.stop_name, []).append({"event": ev, "dict": ev_dict})

    stops = []
    for stop_name in _stop_order(db, [t.id for t in trips]):
        items = by_stop.get(stop_name, [])
        abnormal = [(i["event"], i["dict"]) for i in items if i["event"].status != "normal"]
        bunching_count = sum(1 for e, _ in abnormal if e.status == "bunching")
        large_gap_count = sum(1 for e, _ in abnormal if e.status == "large_gap")
        worst = None
        if abnormal:
            # 偏离幅度相同则保留时间轴上最早出现的一条（max 在并列时保留首个）
            _, worst_dict = max(abnormal, key=lambda x: _severity(x[0], line))
            worst = worst_dict
        stops.append({
            "stop_name": stop_name,
            "bunching_count": bunching_count,
            "large_gap_count": large_gap_count,
            "worst": worst,
        })
    return {
        "line_id": line_id,
        "line_code": line.code,
        "thresholds": {
            "planned_headway_min": line.planned_headway_min,
            "bunch_threshold": line.bunch_threshold,
            "large_threshold": line.large_threshold,
        },
        "stops": stops,
        # 只读的全量事件明细，供班次页条带展示；同样不落任何库。
        "events": event_dicts,
    }


@router.post("/commit")
def commit_stop(body: CommitIn, db: Session = Depends(get_db)):
    """恰好勾选一个站后提交：提交瞬间按当前到站与阈值重算该站，异常才落库。"""
    if len(body.stop_names) == 0:
        raise HTTPException(status_code=400,
                            detail="提交被拒绝：未勾选任何站点。请在预览结果中勾选恰好一个站点后再提交。")
    if len(body.stop_names) > 1:
        names = "、".join(body.stop_names)
        raise HTTPException(
            status_code=400,
            detail=f"提交被拒绝：一次只能勾选一个站点，当前勾选了 {len(body.stop_names)} 个（{names}）。请只保留一个勾选后再提交。",
        )
    stop_name = body.stop_names[0]
    if not stop_name.strip():
        raise HTTPException(status_code=400, detail="提交被拒绝：站点名称不能为空。")

    line = _line_or_404(db, body.line_id)

    known_stops = {name for (name,) in db.execute(
        select(Arrival.stop_name)
        .join(Trip, Trip.id == Arrival.trip_id)
        .where(Trip.line_id == line.id)
        .distinct()
    ).all()}
    if stop_name not in known_stops:
        raise HTTPException(status_code=404,
                            detail=f"站点「{stop_name}」不属于线路 {line.code}，提交失败。")

    # 关键：这里重新查库重算，预览返回过的数字不会被原样写入。
    payload, _ = _trips_and_arrivals(db, line.id, stop_name)
    events = detect_bunching(payload, line.planned_headway_min, line.bunch_threshold, line.large_threshold)
    data = events_to_dicts(events)
    abnormal = [e for e in data if e["status"] != "normal"]
    if not abnormal:
        raise HTTPException(
            status_code=422,
            detail=f"站点「{stop_name}」在提交瞬间未检测到串车或大间隔异常，拒绝落库，不生成空事件报告。",
        )

    report = BunchReport(line_id=line.id, stop_name=stop_name, created_at=datetime.utcnow(),
                         summary_json=json.dumps(data, ensure_ascii=False))
    db.add(report)
    db.commit()
    db.refresh(report)
    return {"id": report.id, "line_id": line.id, "stop_name": stop_name,
            "created_at": report.created_at.isoformat(), "events": data}


@router.get("/suggestions")
def suggestions(line_id: int, db: Session = Depends(get_db)):
    """建议页只点名已落库报告中的单站异常；同站重复提交时以最新报告刷新。"""
    _line_or_404(db, line_id)
    reports = db.scalars(
        select(BunchReport).where(BunchReport.line_id == line_id).order_by(BunchReport.id)
    ).all()
    latest: dict[tuple, dict] = {}
    for r in reports:
        for e in json.loads(r.summary_json):
            if e["status"] == "normal":
                continue
            key = (r.stop_name, e["earlier_trip"], e["later_trip"])
            latest[key] = {**e, "report_id": r.id, "committed_at": r.created_at.isoformat()}
    return {"line_id": line_id, "suggestions": list(latest.values())}


@router.get("/timeline")
def timeline(line_id: int, db: Session = Depends(get_db)):
    """时间轴只为已落库报告涉及的站点生成点；同站再次提交则用最新报告刷新该站点。"""
    _line_or_404(db, line_id)
    reports = db.scalars(
        select(BunchReport).where(BunchReport.line_id == line_id).order_by(BunchReport.id)
    ).all()

    # 每个站只保留最新一份报告的事件 → 新增站点 / 刷新同站
    latest_by_stop: dict[str, dict] = {}
    for r in reports:
        events = json.loads(r.summary_json)
        if events:
            latest_by_stop[r.stop_name] = {
                "report_id": r.id, "created_at": r.created_at.isoformat(), "events": events,
            }

    trip_nos = {e["earlier_trip"] for v in latest_by_stop.values() for e in v["events"]}
    trip_nos |= {e["later_trip"] for v in latest_by_stop.values() for e in v["events"]}
    trips = db.scalars(
        select(Trip).where(Trip.line_id == line_id, Trip.trip_no.in_(sorted(trip_nos) or [""]))
    ).all() if trip_nos else []
    trip_id_by_no = {t.trip_no: t.id for t in trips}

    out_stops = []
    for stop_name, payload_report in latest_by_stop.items():
        events = payload_report["events"]
        ordered_trip_nos: list[str] = []
        for i, e in enumerate(events):
            if i == 0:
                ordered_trip_nos.append(e["earlier_trip"])
            ordered_trip_nos.append(e["later_trip"])

        arrivals = db.scalars(
            select(Arrival).where(
                Arrival.trip_id.in_([trip_id_by_no[n] for n in ordered_trip_nos if n in trip_id_by_no]),
                Arrival.stop_name == stop_name,
            )
        ).all()
        arrive_by_trip = {a.trip_id: a for a in arrivals}

        marks = []
        gap_from_prev: dict[str, dict] = {}
        for e in events:
            gap_from_prev[e["later_trip"]] = {"gap_min": e["gap_min"], "status": e["status"]}
        for no in ordered_trip_nos:
            tid = trip_id_by_no.get(no)
            a = arrive_by_trip.get(tid) if tid is not None else None
            if a is None:
                continue
            marks.append({
                "trip_no": no,
                "actual_arrive": a.actual_arrive.isoformat(),
                "gap_from_prev": gap_from_prev.get(no),
            })
        marks.sort(key=lambda m: m["actual_arrive"])
        if marks:
            t0 = datetime.fromisoformat(marks[0]["actual_arrive"])
            tn = datetime.fromisoformat(marks[-1]["actual_arrive"])
            span = max((tn - t0).total_seconds(), 1)
            for m in marks:
                m["pct"] = round(
                    (datetime.fromisoformat(m["actual_arrive"]) - t0).total_seconds() / span * 100, 2
                )
        out_stops.append({
            "stop_name": stop_name,
            "report_id": payload_report["report_id"],
            "committed_at": payload_report["created_at"],
            "marks": marks,
        })

    out_stops.sort(key=lambda s: s["report_id"])
    return {"line_id": line_id, "stops": out_stops}
