"""Отдельная интеграция: TEST_POSTGRES_URL должен указывать на тестовую БД."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from zavtra.database import Database
from zavtra.service import Service


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL не задан")
def test_postgres_end_to_end_and_concurrent_confirm():
    db = Database(os.environ["TEST_POSTGRES_URL"])
    schema = "zavtra_test_" + uuid.uuid4().hex
    with db.engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    db.engine.update_execution_options(schema_translate_map={None: schema})
    try:
        db.initialize()
        app = Service(db, lambda: datetime(2026, 10, 17, 18, tzinfo=UTC))
        _, code = app.create_invite()
        app.join(100, code)
        app.load_demo(100)
        app.record_stock(100, {"Курица": 2, "Лаваш": 60, "Соус": 0})
        run = app.recommend(100)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: app.confirm(100, run["id"]), range(2)))
        assert results[0]["id"] == results[1]["id"]
        assert app.order(100)["id"] == results[0]["id"]
    finally:
        with db.engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        db.engine.dispose()
