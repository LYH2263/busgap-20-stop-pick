"""端到端：B12 先预览各站、再只落一站；报告/时间轴/建议同一口径。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.seed import seed_if_empty


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    seed_if_empty(db)
    db.close()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _stop(preview, name):
    return next(s for s in preview["stops"] if s["stop_name"] == name)


def _report_count(client):
    return len(client.get("/api/reports").json())


def test_seed_preview_exposes_bunching_and_large_gap(client):
    r = client.get("/api/reports/preview?line_id=1")
    assert r.status_code == 200
    data = r.json()
    civic = _stop(data, "市民中心")
    station = _stop(data, "火车站")
    # 种子预览须同时露出市民中心串车与火车站大间隔
    assert civic["bunching_count"] >= 1
    assert civic["large_gap_count"] == 0
    assert civic["worst"]["status"] == "bunching"
    assert civic["worst"]["gap_min"] == 2.0
    assert civic["worst"]["earlier_trip"] == "T01"
    assert civic["worst"]["later_trip"] == "T02"
    assert station["large_gap_count"] >= 1
    assert station["bunching_count"] == 0
    assert station["worst"]["status"] == "large_gap"
    assert station["worst"]["gap_min"] == 16.0
    # 其余站无异常
    assert _stop(data, "起点站")["worst"] is None
    assert _stop(data, "终点站")["worst"] is None


def test_preview_is_read_only_and_idempotent(client):
    assert _report_count(client) == 0
    first = client.get("/api/reports/preview?line_id=1").json()
    second = client.get("/api/reports/preview?line_id=1").json()
    # 连点两次预览：报告行数都不得增加，内容一致
    assert _report_count(client) == 0
    assert first == second


def test_commit_zero_selection_rejected_and_distinct_message(client):
    r = client.post("/api/reports/commit", json={"line_id": 1, "stop_names": []})
    assert r.status_code == 400
    assert "未勾选任何站点" in r.json()["detail"]
    assert _report_count(client) == 0


def test_commit_multiple_selection_rejected_with_distinct_message(client):
    r = client.post("/api/reports/commit",
                    json={"line_id": 1, "stop_names": ["市民中心", "火车站"]})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "一次只能勾选一个站点" in detail
    assert "2" in detail
    # 与零勾选文案必须能区分
    assert "未勾选任何站点" not in detail
    assert _report_count(client) == 0


def test_commit_one_stop_lands_only_that_stop_everywhere(client):
    r = client.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    assert r.status_code == 200
    report = r.json()
    assert report["stop_name"] == "市民中心"
    abnormal = [e for e in report["events"] if e["status"] != "normal"]
    assert abnormal and all(e["stop_name"] == "市民中心" for e in report["events"])
    bunch = next(e for e in abnormal if e["status"] == "bunching")
    assert (bunch["earlier_trip"], bunch["later_trip"], bunch["gap_min"]) == ("T01", "T02", 2.0)

    # 预览仍不增加报告行
    before = _report_count(client)
    client.get("/api/reports/preview?line_id=1")
    client.get("/api/reports/preview?line_id=1")
    assert _report_count(client) == before == 1

    # 报告列表：只含该站事件
    reports = client.get("/api/reports").json()
    assert len(reports) == 1
    assert all(e["stop_name"] == "市民中心" for e in reports[0]["events"])

    # 建议页：只点名市民中心，火车站大间隔不得出现
    tips = client.get("/api/reports/suggestions?line_id=1").json()["suggestions"]
    assert tips and all(t["stop_name"] == "市民中心" for t in tips)
    assert not any(t["stop_name"] == "火车站" for t in tips)

    # 时间轴：只有市民中心一个站点，点与报告前后班次/间隔逐条对得上
    tl = client.get("/api/reports/timeline?line_id=1").json()
    assert [s["stop_name"] for s in tl["stops"]] == ["市民中心"]
    marks = tl["stops"][0]["marks"]
    by_trip = {m["trip_no"]: m for m in marks}
    assert set(by_trip) >= {"T01", "T02"}
    assert by_trip["T01"]["gap_from_prev"] is None
    assert by_trip["T02"]["gap_from_prev"] == {"gap_min": 2.0, "status": "bunching"}
    assert by_trip["T01"]["actual_arrive"].endswith("07:06:00")
    assert by_trip["T02"]["actual_arrive"].endswith("07:08:00")


def _arrival_id(client, trip_no, stop_name):
    rows = client.get("/api/arrivals?line_id=1").json()
    return next(a["id"] for a in rows if a["trip_no"] == trip_no and a["stop_name"] == stop_name)


def test_commit_when_stop_has_no_anomaly_is_rejected(client):
    aid = _arrival_id(client, "T02", "市民中心")
    # 把 T02 推到 07:14，与 T01 间隔 8 分钟：市民中心已无异常
    r = client.patch(f"/api/arrivals/{aid}", json={"actual_arrive": "2026-09-17T07:14:00"})
    assert r.status_code == 200
    # 预览也同步显示无异常（预览后改到站，预览不缓存）
    civic = _stop(client.get("/api/reports/preview?line_id=1").json(), "市民中心")
    assert civic["bunching_count"] == 0 and civic["large_gap_count"] == 0
    # 提交：明确拒落、不落空事件报告
    r = client.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    assert r.status_code == 422
    assert "未检测到" in r.json()["detail"] and "拒绝落库" in r.json()["detail"]
    assert _report_count(client) == 0


def test_recompute_at_commit_uses_current_arrivals_and_refreshes_all_views(client):
    # 先落一份市民中心（2 分钟串车）
    client.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})

    aid = _arrival_id(client, "T02", "市民中心")
    # 预览后改到站：间隔改为 1 分钟
    client.patch(f"/api/arrivals/{aid}", json={"actual_arrive": "2026-09-17T07:07:00"})
    r = client.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    assert r.status_code == 200
    assert r.json()["id"] == 2
    bunch = next(e for e in r.json()["events"] if e["status"] == "bunching")
    assert bunch["gap_min"] == 1.0  # 用的是新时刻，不是预览缓存里的 2.0

    # 时间轴刷新该站为最新报告，间隔跟新时刻
    tl = client.get("/api/reports/timeline?line_id=1").json()
    assert len(tl["stops"]) == 1 and tl["stops"][0]["report_id"] == 2
    assert tl["stops"][0]["marks"][1]["gap_from_prev"]["gap_min"] == 1.0
    # 建议同样跟新时刻，且仍不出现火车站
    tips = client.get("/api/reports/suggestions?line_id=1").json()["suggestions"]
    assert len(tips) == 1
    assert tips[0]["stop_name"] == "市民中心" and tips[0]["gap_min"] == 1.0
    assert tips[0]["report_id"] == 2


def test_bad_line_commit_failure_keeps_prior_report_and_timeline(client):
    ok = client.post("/api/reports/commit", json={"line_id": 1, "stop_names": ["市民中心"]})
    assert ok.status_code == 200
    bad = client.post("/api/reports/commit", json={"line_id": 999, "stop_names": ["市民中心"]})
    assert bad.status_code == 404
    assert "非法线路编号" in bad.json()["detail"]
    # 失败不得抹掉此前已成功的单站报告与时间轴
    reports = client.get("/api/reports").json()
    assert len(reports) == 1 and reports[0]["stop_name"] == "市民中心"
    tl = client.get("/api/reports/timeline?line_id=1").json()
    assert [s["stop_name"] for s in tl["stops"]] == ["市民中心"]


def test_preview_bad_line_is_rejected(client):
    r = client.get("/api/reports/preview?line_id=999")
    assert r.status_code == 404
