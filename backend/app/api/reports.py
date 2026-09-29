import json
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Arrival, BunchReport, Line, Trip
from app.services.bunch_engine import GapEvent, detect_bunching, events_to_dicts

router = APIRouter(prefix="/reports", tags=["reports"])

ABNORMAL_STATUSES = ("bunching", "large_gap")
STATUS_LABEL = {"bunching": "串车", "large_gap": "大间隔", "normal": "正常"}


def _load_line(db: Session, line_id: int) -> Line:
    line = db.get(Line, line_id)
    if not line:
        raise HTTPException(status_code=404, detail=f"线路编号 {line_id} 非法（不存在），提交失败，原有报告保持不变。")
    return line


def _line_arrivals(db: Session, line_id: int, stop_name: str | None = None):
    """取线路（可限单站）当前全部到站，返回 (payload, 到站行带trip_no)。

    每次调用都直接读库，不保留任何预览缓存，保证提交瞬间按当前数据重算。
    """
    trips = db.scalars(select(Trip).where(Trip.line_id == line_id)).all()
    trip_no_map = {t.id: t.trip_no for t in trips}
    if not trip_no_map:
        return [], []
    q = select(Arrival).where(Arrival.trip_id.in_(list(trip_no_map)))
    if stop_name is not None:
        q = q.where(Arrival.stop_name == stop_name)
    rows = db.scalars(q).all()
    payload = [{"stop_name": a.stop_name, "trip_no": trip_no_map[a.trip_id],
                "actual_arrive": a.actual_arrive} for a in rows]
    return payload, rows


def _detect(db: Session, line: Line, stop_name: str | None = None) -> list[GapEvent]:
    payload, _ = _line_arrivals(db, line.id, stop_name)
    return detect_bunching(payload, line.planned_headway_min, line.bunch_threshold, line.large_threshold)


def _marks_from_rows(rows: list[Arrival], trip_no_map: dict[int, str],
                     events: list[dict] | None = None) -> list[dict]:
    rows = sorted(rows, key=lambda a: a.actual_arrive)
    if not rows:
        return []
    # 以「后班次」挂状态，时间轴颜色与报告事件同一口径，不由前端按位置猜
    later_status = {e["later_trip"]: e["status"]
                    for e in (events or []) if e.get("status") in ABNORMAL_STATUSES}
    t0 = rows[0].actual_arrive
    span = max((rows[-1].actual_arrive - t0).total_seconds(), 1.0)
    return [{"trip_no": trip_no_map[a.trip_id],
             "actual_arrive": a.actual_arrive.isoformat(),
             "status": later_status.get(trip_no_map[a.trip_id], "normal"),
             "pct": round((a.actual_arrive - t0).total_seconds() / span * 100, 2)} for a in rows]


def _worst_event(events: list[GapEvent]) -> GapEvent | None:
    """最严重一条：大间隔优先于串车；同类中偏离计划最极端者。"""
    abnormal = [e for e in events if e.status in ABNORMAL_STATUSES]
    if not abnormal:
        return None
    large = [e for e in abnormal if e.status == "large_gap"]
    if large:
        return max(large, key=lambda e: e.gap_min)
    return min((e for e in abnormal if e.status == "bunching"), key=lambda e: e.gap_min)


@router.get("/preview")
def preview(line_id: int, db: Session = Depends(get_db)):
    """只读预览：按站给出串车条数、大间隔条数、最严重一条的间隔与状态。

    绝不写库——连点多次，报告行数不变。
    """
    line = _load_line(db, line_id)
    events = _detect(db, line)

    # 站点顺序按 stop_seq，避免报告顺序漂移
    _, rows = _line_arrivals(db, line_id)
    stop_seq = {a.stop_name: a.stop_seq for a in rows}
    by_stop: dict[str, list[GapEvent]] = {}
    for e in events:
        by_stop.setdefault(e.stop_name, []).append(e)

    stops = []
    for stop_name in sorted(by_stop, key=lambda s: (stop_seq.get(s, 0), s)):
        items = by_stop[stop_name]
        bunching = [e for e in items if e.status == "bunching"]
        large = [e for e in items if e.status == "large_gap"]
        worst = _worst_event(items)
        stops.append({
            "stop_name": stop_name,
            "bunching_count": len(bunching),
            "large_gap_count": len(large),
            "abnormal_count": len(bunching) + len(large),
            "worst": None if worst is None else {
                "gap_min": worst.gap_min,
                "status": worst.status,
                "status_label": STATUS_LABEL[worst.status],
                "earlier_trip": worst.earlier_trip,
                "later_trip": worst.later_trip,
            },
        })
    return {
        "line_id": line_id,
        "planned_headway_min": line.planned_headway_min,
        "bunch_threshold": line.bunch_threshold,
        "large_threshold": line.large_threshold,
        "stops": stops,
    }


@router.get("/events")
def current_events(line_id: int, stop_name: str | None = None, db: Session = Depends(get_db)):
    """只读：按当前到站实时计算间隔事件（不落任何报告），供班次页条带展示。"""
    line = _load_line(db, line_id)
    return {"line_id": line_id, "events": events_to_dicts(_detect(db, line, stop_name))}


@router.post("/commit")
def commit_stop(payload: dict = Body(default={}), db: Session = Depends(get_db)):
    """先预览、再只落一站：必须恰好勾选一个站。

    - 零勾选 / 一次勾两个及以上：分别 400，文案可区分，且不写任何报告；
    - 非法线路编号：404，不动已有报告；
    - 提交瞬间按当前到站与阈值重算该站，禁止使用预览缓存数字；
    - 重算后该站已无异常：409 明确拒落，不写空事件报告；
    - 落库报告只含该站事件与该站时间轴快照。
    """
    line_id = payload.get("line_id")
    stop_names = payload.get("stop_names")

    # 勾选校验先于一切写操作（也先于线路查找）
    if not isinstance(stop_names, list) or len(stop_names) == 0:
        raise HTTPException(status_code=400,
                            detail="未勾选任何站点：请先在预览结果中勾选且仅勾选一个站点，再提交落库。")
    if len(stop_names) > 1:
        raise HTTPException(status_code=400,
                            detail=f"一次只能提交一个站点，当前勾选了 {len(stop_names)} 个（{'、'.join(map(str, stop_names))}），请只保留一个。")
    stop_name = stop_names[0]
    if not isinstance(stop_name, str) or not stop_name.strip():
        raise HTTPException(status_code=400, detail="勾选的站点名称无效，请重新选择一个站点。")
    stop_name = stop_name.strip()

    if not isinstance(line_id, int):
        raise HTTPException(status_code=400, detail="线路编号无效，提交失败，原有报告保持不变。")
    line = _load_line(db, line_id)  # 非法线路 → 404

    # 提交瞬间重算：重新读当前到站、当前阈值，不使用预览缓存
    events = _detect(db, line, stop_name)
    data = events_to_dicts(events)
    abnormal = [e for e in data if e["status"] in ABNORMAL_STATUSES]
    if not abnormal:
        raise HTTPException(status_code=409,
                            detail=f"站点「{stop_name}」按提交瞬间的到站与阈值重算后已无串车或大间隔异常，拒绝落库，未生成空事件报告。")

    _, rows = _line_arrivals(db, line_id, stop_name)
    trip_no_map = {t.id: t.trip_no for t in db.scalars(select(Trip).where(Trip.line_id == line_id)).all()}
    marks = _marks_from_rows(rows, trip_no_map, data)

    snapshot = {"events": data, "marks": marks}
    report = BunchReport(line_id=line_id, stop_name=stop_name, created_at=datetime.utcnow(),
                         summary_json=json.dumps(snapshot, ensure_ascii=False))
    db.add(report)
    db.commit()
    db.refresh(report)
    return {"id": report.id, "line_id": line_id, "stop_name": stop_name,
            "created_at": report.created_at.isoformat(), "events": data, "marks": marks}


def _parse_snapshot(raw: str) -> tuple[list[dict], list[dict]]:
    snap = json.loads(raw)
    if isinstance(snap, dict):
        return snap.get("events", []), snap.get("marks", [])
    return snap, []  # 兼容早期裸 events-list 格式


def _latest_report_per_stop(db: Session, line_id: int) -> list[BunchReport]:
    rows = db.scalars(
        select(BunchReport).where(BunchReport.line_id == line_id).order_by(BunchReport.id)
    ).all()
    latest: dict[str, BunchReport] = {}
    for r in rows:  # 同站按 id 升序覆盖 → 留下最新一份
        latest[r.stop_name] = r
    return list(latest.values())


@router.get("")
def list_reports(line_id: int | None = None, db: Session = Depends(get_db)):
    q = select(BunchReport).order_by(BunchReport.id.desc())
    if line_id is not None:
        q = q.where(BunchReport.line_id == line_id)
    out = []
    for r in db.scalars(q).all():
        events, marks = _parse_snapshot(r.summary_json)
        out.append({"id": r.id, "line_id": r.line_id, "stop_name": r.stop_name,
                    "created_at": r.created_at.isoformat(), "events": events, "marks": marks})
    return out


@router.get("/suggestions")
def suggestions(line_id: int, db: Session = Depends(get_db)):
    """建议页只点名已落库报告中的异常；每站以最新一份报告为准（刷新语义）。"""
    tips = []
    for r in _latest_report_per_stop(db, line_id):
        events, _ = _parse_snapshot(r.summary_json)
        for e in events:
            if e["status"] in ABNORMAL_STATUSES:
                tips.append({"report_id": r.id, **e})
    tips.sort(key=lambda e: (e["stop_name"], e["earlier_trip"], e["later_trip"]))
    return {"line_id": line_id, "suggestions": tips}


@router.get("/timeline")
def timeline(line_id: int, db: Session = Depends(get_db)):
    """时间轴只展示已落库报告的站点快照；每站以最新一份报告为准。

    未提交落库的站点不会出现；提交新站只新增/刷新该站对应点，不碰其它站。
    """
    stops = []
    for r in _latest_report_per_stop(db, line_id):
        _, marks = _parse_snapshot(r.summary_json)
        stops.append({"report_id": r.id, "stop_name": r.stop_name, "marks": marks})
    stops.sort(key=lambda s: s["stop_name"])
    return {"line_id": line_id, "stops": stops}
