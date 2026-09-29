# BusGap 公交串车检测

对比计划发车间隔与实际到站间隔，识别串车与大间隔，并给出调班建议。

技术栈：Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL / Vue 3 / TypeScript / Vite

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4600 |
| API | http://localhost:9600 |
| API 文档 | http://localhost:9600/docs |
| Postgres | localhost:5447 |

健康检查：`GET http://localhost:9600/api/health`

## 使用说明

1. 在「线路」查看运营线路与阈值阈值。
2. 在「班次」「到站」核对计划与实际到站时间（到站页可修改实际到站时刻）。
3. 打开「串车报告」：先点「预览各站」只读查看每站串车条数、大间隔条数与最严重事件（预览不落库，重复点击报告行数不增加）。
4. 在预览表中**勾选恰好一个站点**后「提交」：零勾选或一次勾选多个都会被拒绝（两种提示文案不同）；提交瞬间按当前到站与阈值重算该站，不写预览缓存；该站已无异常则拒绝落库、不产生空报告。
5. 落库后，「串车报告」只含该站事件，「时间轴」只为该站新增/刷新点，「建议」只点名该站异常；三处同一口径，均来自已落库报告。

## 开发与测试

```bash
docker compose exec api pytest -q
```
