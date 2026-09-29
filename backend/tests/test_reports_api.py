from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import Arrival, BunchReport, Line, Trip
from app.services.seed import seed_if_empty


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        db = TestingSession()
        seed_if_empty(db)
        db.close()
        yield c, TestingSession
    app.dependency_overrides.clear()


def _report_count(Session):
    with Session() as db:
        return len(db.scalars(select(BunchReport)).all())


# ---------- 预览：只读、按站汇总、种子必须露出两处异常 ----------

def test_preview_is_read_only_and_repeatable(client):
    c, Session = client
    assert _report_count(Session) == 0
    r1 = c.get("/api/reports/preview?line_id=1")
    r2 = c.get("/api/reports/preview?line_id=1")
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    # 连点两次预览，报告行数都不得增加
    assert _report_count(Session) == 0


def test_seed_preview_exposes_both_abnormal_stops(client):
    c, _ = client
    stops = {s["stop_name"]: s for s in c.get("/api/reports/preview?line_id=1").json()["stops"]}
    assert set(stops) == {"起点站", "市民中心", "火车站", "终点站"}

    civic = stops["市民中心"]
    assert civic["bunching_count"] == 1
    assert civic["large_gap_count"] == 0
    assert civic["worst"]["status"] == "bunching"
    assert civic["worst"]["gap_min"] == 2.0
    assert (civic["worst"]["earlier_trip"], civic["worst"]["later_trip"]) == ("T01", "T02")

    station = stops["火车站"]
    assert station["large_gap_count"] == 1
    assert station["bunching_count"] == 0
    assert station["worst"]["status"] == "large_gap"
    assert station["worst"]["gap_min"] == 16.0
    assert (station["worst"]["earlier_trip"], station["worst"]["later_trip"]) == ("T02", "T03")

    assert stops["起点站"]["abnormal_count"] == 0 and stops["起点站"]["worst"] is None
    assert stops["终点站"]["abnormal_count"] == 0 and stops["终点站"]["worst"] is None


# ---------- 勾选校验：零 / 多选文案可区分，行数不变 ----------

def test_commit_zero_selection_rejected_distinct_message(client):
    c, Session = client
    r = c.post("/api/reports/commit", json={"line_id": 1, "stop_names": []})
    assert r.status_code == 400
    assert "未勾选" in r.json()["detail"]
    assert _report_count(Session) == 0


def test_commit_multi_selection_rejected_distinct_message(client):
    c, Session = client
    r = c.post("/api/reports/commit",
               json={"line_id": 1, "stop_names": ["市民中心", "火车站"]})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "只能提交一个" in detail and "2" in detail
    # 与零勾选文案必须能区分
    assert "未勾选" not in detail
    assert _report_count(Session) == 0


def test_commit_three_selection_rejected(client):
    c, Session = client
    r = c.post("/api/reports/commit",
               json={"line_id": 1, "stop_names": ["市民中心", "火车站", "终点站"]})
    assert r.status_code == 400
    assert "3" in r.json()["detail"]
    assert _report_count(Session) == 0


# ---------- 单站落库：只含该站、三处同口径 ----------

def test_commit_single_stop_isolates_report(client):
    c, Session = client
    r = c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    assert r.status_code == 200
    body = r.json()
    assert body["stop_name"] == "市民中心"
    assert {e["stop_name"] for e in body["events"]} == {"市民中心"}
    assert len(body["events"]) == 3  # 3 个相邻间隔，其中 1 条串车
    assert {m["trip_no"] for m in body["marks"]} == {"T01", "T02", "T03", "T04"}
    assert _report_count(Session) == 1


def test_after_civic_commit_no_station_large_gap_in_reports_or_suggestions(client):
    c, Session = client
    c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})

    reports = c.get("/api/reports?line_id=1").json()
    assert len(reports) == 1
    assert reports[0]["stop_name"] == "市民中心"
    assert not any(e["status"] == "large_gap" for e in reports[0]["events"])

    tips = c.get("/api/reports/suggestions?line_id=1").json()["suggestions"]
    named = {t["stop_name"] for t in tips}
    assert named == {"市民中心"}
    assert "火车站" not in named
    assert all(t["status"] == "bunching" for t in tips)


def test_timeline_only_has_committed_stop_and_marks_match_report(client):
    c, _ = client
    c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})

    tl = c.get("/api/reports/timeline?line_id=1").json()
    assert [s["stop_name"] for s in tl["stops"]] == ["市民中心"]
    marks = tl["stops"][0]["marks"]
    report_events = c.get("/api/reports?line_id=1").json()[0]["events"]

    by_trip = {m["trip_no"]: m for m in marks}
    bunch = next(e for e in report_events if e["status"] == "bunching")
    # 时间轴市民中心点能对上报告里的前后班次 T01→T02
    assert (bunch["earlier_trip"], bunch["later_trip"]) == ("T01", "T02")
    assert by_trip["T01"]["actual_arrive"] < by_trip["T02"]["actual_arrive"]
    t01 = datetime.fromisoformat(by_trip["T01"]["actual_arrive"])
    t02 = datetime.fromisoformat(by_trip["T02"]["actual_arrive"])
    assert (t02 - t01) == timedelta(minutes=2)
    # pct 单调
    assert [m["pct"] for m in marks] == sorted(m["pct"] for m in marks)


def test_commit_second_stop_only_adds_that_stop(client):
    c, _ = client
    c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    r = c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["火车站"]})
    assert r.status_code == 200
    assert {e["stop_name"] for e in r.json()["events"]} == {"火车站"}

    reports = c.get("/api/reports?line_id=1").json()
    assert {rep["stop_name"] for rep in reports} == {"市民中心", "火车站"}
    tl_stops = {s["stop_name"] for s in c.get("/api/reports/timeline?line_id=1").json()["stops"]}
    assert tl_stops == {"市民中心", "火车站"}
    tip_stops = {t["stop_name"] for t in c.get("/api/reports/suggestions?line_id=1").json()["suggestions"]}
    assert tip_stops == {"市民中心", "火车站"}


def test_recommit_same_stop_refreshes_latest_snapshot(client):
    c, Session = client
    c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    # 改到站使市民中心恢复正常，再重新提交该站 → 拒落（无异常），旧报告保留
    db = Session()
    t2 = db.scalars(select(Trip).where(Trip.trip_no == "T02")).first()
    a = db.scalars(select(Arrival).where(Arrival.trip_id == t2.id, Arrival.stop_name == "市民中心")).first()
    a.actual_arrive = datetime(2026, 9, 17, 7, 11)  # T01=07, T02=11 → 4 分钟，正常
    db.commit()
    db.close()

    r = c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    assert r.status_code == 409
    assert "无" in r.json()["detail"] and "异常" in r.json()["detail"]
    # 拒绝落空事件：报告仍是原来 1 份
    assert _report_count(Session) == 1
    tips = c.get("/api/reports/suggestions?line_id=1").json()["suggestions"]
    assert any(t["stop_name"] == "市民中心" for t in tips)  # 旧报告仍在，不被空报告覆盖


# ---------- 提交瞬间重算，不用预览缓存 ----------

def test_commit_recomputes_after_arrival_change(client):
    c, Session = client
    # 先预览（市民中心串车 2 分钟）
    preview = c.get("/api/reports/preview?line_id=1").json()
    civic_before = next(s for s in preview["stops"] if s["stop_name"] == "市民中心")
    assert civic_before["worst"]["gap_min"] == 2.0

    # 预览后改到站再提交：T02 由 9 分改到 10 分 → 间隔 3 分钟，恰不小于阈值 → 正常
    db = Session()
    t2 = db.scalars(select(Trip).where(Trip.trip_no == "T02")).first()
    a = db.scalars(select(Arrival).where(Arrival.trip_id == t2.id, Arrival.stop_name == "市民中心")).first()
    a.actual_arrive = datetime(2026, 9, 17, 7, 10)
    db.commit()
    db.close()

    r = c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    assert r.status_code == 409
    assert _report_count(Session) == 0
    # 三处跟新时刻：预览重新算也应无异常
    civic_after = next(s for s in c.get("/api/reports/preview?line_id=1").json()["stops"]
                       if s["stop_name"] == "市民中心")
    assert civic_after["abnormal_count"] == 0


def test_commit_recomputes_with_new_abnormal_time(client):
    c, _ = client
    c.get("/api/reports/preview?line_id=1")  # 预热缓存（即便有也不许用）
    db = next(app.dependency_overrides[get_db]())
    t2 = db.scalars(select(Trip).where(Trip.trip_no == "T02")).first()
    a = db.scalars(select(Arrival).where(Arrival.trip_id == t2.id, Arrival.stop_name == "市民中心")).first()
    a.actual_arrive = datetime(2026, 9, 17, 7, 8)  # 间隔变成 1 分钟
    db.commit()
    db.close()

    r = c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    assert r.status_code == 200
    bunch = [e for e in r.json()["events"] if e["status"] == "bunching"]
    assert len(bunch) == 1 and bunch[0]["gap_min"] == 1.0
    # 时间轴快照也是新时刻
    m = {x["trip_no"]: x for x in r.json()["marks"]}
    assert m["T02"]["actual_arrive"] == datetime(2026, 9, 17, 7, 8).isoformat()


# ---------- 干净站：无异常明确拒落 ----------

def test_commit_clean_stop_rejected(client):
    c, Session = client
    r = c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["起点站"]})
    assert r.status_code == 409
    assert "无" in r.json()["detail"]
    assert _report_count(Session) == 0
    tl = c.get("/api/reports/timeline?line_id=1").json()["stops"]
    assert tl == []


# ---------- 非法线路：404 且不抹已有单站报告与时间轴 ----------

def test_invalid_line_does_not_wipe_existing_report(client):
    c, Session = client
    ok = c.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    assert ok.status_code == 200

    bad = c.post("/api/reports/commit", json={"line_id": 999, "stop_names": ["市民中心"]})
    assert bad.status_code == 404
    assert _report_count(Session) == 1

    reports = c.get("/api/reports?line_id=1").json()
    assert len(reports) == 1 and reports[0]["stop_name"] == "市民中心"
    tl = c.get("/api/reports/timeline?line_id=1").json()["stops"]
    assert [s["stop_name"] for s in tl] == ["市民中心"]
    assert len(tl[0]["marks"]) == 4

    # 预览非法线路也不写库
    assert c.get("/api/reports/preview?line_id=999").status_code == 404
    assert _report_count(Session) == 1


def test_suggestions_never_writes(client):
    c, Session = client
    assert c.get("/api/reports/suggestions?line_id=1").status_code == 200
    assert c.get("/api/reports/timeline?line_id=1").status_code == 200
    assert c.get("/api/reports/events?line_id=1").status_code == 200
    assert _report_count(Session) == 0


def test_events_endpoint_read_only_and_filtered(client):
    c, _ = client
    r = c.get("/api/reports/events?line_id=1&stop_name=火车站")
    events = r.json()["events"]
    assert {e["stop_name"] for e in events} == {"火车站"}
    assert sum(e["status"] == "large_gap" for e in events) == 1
