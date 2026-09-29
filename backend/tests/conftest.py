import os

# 测试一律使用进程内 sqlite，与环境里可能存在的 postgres 完全隔离；
# 必须在导入 app.* 之前设置（app.database 在导入时按 DATABASE_URL 建 engine）。
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SEED_ON_EMPTY"] = "false"
