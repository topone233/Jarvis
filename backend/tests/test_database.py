from __future__ import annotations

import threading
from pathlib import Path

from app.database import Database


def test_threads_share_one_connection_without_losing_writes(tmp_path: Path) -> None:
    """真线程同时走 transaction 和 fetchone：写全部落库，无一处报错。

    共享连接成立的前提是锁覆盖每个入口——sqlite3 连接跨线程使用必须由
    调用方串行化，任何入口漏加锁都会在这个测试里以异常或丢写露馅
    （线程里的异常会被收集断言，不会静默溜走）。
    """
    database = Database(tmp_path)
    database.initialize()
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(25):
                with database.transaction() as connection:
                    connection.execute(
                        "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                        (f"key-{n}-{i}", "{}", "2026-01-01T00:00:00Z"),
                    )
                database.fetchone("SELECT COUNT(*) AS n FROM settings")
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    final = database.fetchone("SELECT COUNT(*) AS n FROM settings")
    assert final is not None
    assert final["n"] == 200


def test_a_failed_transaction_leaves_the_shared_connection_clean(tmp_path: Path) -> None:
    """回滚后连接立即复用：半个事务不能污染下一个调用。

    共享连接不再有「连接关闭顺带丢弃未提交工作」这道防线，回滚就是
    唯一的清理——它必须把连接放回干净状态，而不是留一个挂起的事务
    让下一次不相关的 commit 替它收尾。
    """
    database = Database(tmp_path)
    database.initialize()

    def exploding() -> None:
        with database.transaction() as connection:
            connection.execute(
                "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
                ("half-done", "{}", "2026-01-01T00:00:00Z"),
            )
            raise RuntimeError("炸在中途")

    raised = None
    try:
        exploding()
    except RuntimeError as error:
        raised = error

    assert raised is not None
    # 下一个不相关的事务正常提交，且看不到上一个事务的半个写入。
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO settings(key, value_json, updated_at) VALUES (?, ?, ?)",
            ("after", "{}", "2026-01-01T00:00:00Z"),
        )

    final = database.fetchone("SELECT COUNT(*) AS n FROM settings")
    assert final is not None
    assert final["n"] == 1
