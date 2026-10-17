from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from zavtra.database import Database, Ingredient, Order, Recommendation
from zavtra.domain import UserError, calculate
from zavtra.samples import INGREDIENTS, sales_csv
from zavtra.service import Service


def test_invite_is_single_use_and_expires(service):
    _, code = service.create_invite()
    service.join(100, code)
    with pytest.raises(UserError, match="недействителен"):
        service.join(101, code)
    _, expired = service.create_invite()
    service.clock = lambda: datetime(2026, 10, 19, tzinfo=UTC)
    with pytest.raises(UserError, match="просрочен"):
        service.join(102, expired)


def test_guest_and_other_point_cannot_access_a_recommendation(ready):
    run = ready.recommend(100)
    with pytest.raises(UserError, match="не подключена"):
        ready.info(999)
    _, code = ready.create_invite("Другая точка")
    ready.join(200, code)
    with pytest.raises(UserError, match="не найдена"):
        ready.confirm(200, run["id"])
    with pytest.raises(UserError, match="не найдена"):
        ready.edit(200, run["id"], run["items"][0]["id"], 1)


def test_missing_stock_and_short_history_are_explicit(connected):
    connected.load_demo(100)
    connected.record_stock(100, {"Курица": 2})
    with pytest.raises(UserError, match="Лаваш"):
        connected.recommend(100)


def test_short_history_does_not_produce_forecast(connected):
    connected.import_ingredients(100, INGREDIENTS.encode(), "ingredients.csv")
    connected.import_sales(100, sales_csv(datetime(2026, 10, 17).date(), 20).encode(), "sales.csv")
    connected.record_stock(100, {"Курица": 0, "Лаваш": 0, "Соус": 0})
    with pytest.raises(UserError, match="28"):
        connected.recommend(100)


def test_changed_stock_invalidates_old_buttons(ready):
    old = ready.recommend(100)
    ready.record_stock(100, {"Курица": 4})
    with pytest.raises(UserError, match="неактуальна"):
        ready.confirm(100, old["id"])
    new = ready.recommend(100)
    assert Decimal(new["items"][0]["recommended"]) < Decimal(old["items"][0]["recommended"])


def test_edit_confirm_is_idempotent_and_survives_restart(ready):
    run = ready.recommend(100)
    ready.edit(100, run["id"], run["items"][0]["id"], "8")
    first = ready.confirm(100, run["id"])
    assert ready.confirm(100, run["id"])["id"] == first["id"]
    assert Decimal(first["items"][0]["final"]) == 8
    assert first["items"][0]["recommended"] == run["items"][0]["recommended"]
    with pytest.raises(UserError):
        ready.edit(100, run["id"], run["items"][0]["id"], "9")
    with pytest.raises(UserError):
        ready.record_stock(100, {"Курица": 1})
    url = str(ready.db.engine.url)
    ready.db.engine.dispose()
    reopened = Database(url)
    reopened.initialize()
    try:
        assert Service(reopened, ready.clock).order(100) == first
    finally:
        reopened.engine.dispose()


def test_concurrent_confirm_creates_one_order(ready):
    run = ready.recommend(100)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ready.confirm(100, run["id"]), range(2)))
    assert results[0]["id"] == results[1]["id"]
    with ready.db.session() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1


def test_snapshot_reproduces_result_after_ingredient_price_changes(ready):
    run = ready.recommend(100)
    with ready.db.session() as session:
        snapshot = session.get(Recommendation, run["id"]).snapshot
    ready.import_ingredients(100, INGREDIENTS.replace("1200", "2000").encode(), "ingredients.csv")
    assert calculate(snapshot["input"]) == snapshot["result"]


def test_missing_cu_requires_manual_quantity(ready):
    with ready.db.session.begin() as session:
        ingredient = session.scalar(select(Ingredient).where(Ingredient.name == "Курица"))
        ingredient.cu = None
    run = ready.recommend(100)
    chicken = run["items"][0]
    assert chicken["recommended"] is None
    with pytest.raises(UserError, match="вручную"):
        ready.confirm(100, run["id"])
    ready.edit(100, run["id"], chicken["id"], "0")
    assert ready.confirm(100, run["id"])["items"][0]["final"] == "0"


def test_midnight_makes_yesterdays_recommendation_expired(ready):
    run = ready.recommend(100)
    ready.clock = lambda: datetime(2026, 10, 17, 22, tzinfo=UTC)  # Уже 18-е в Москве.
    with pytest.raises(UserError, match="неактуальна"):
        ready.confirm(100, run["id"])


def test_stale_sales_block_recommendation(ready):
    ready.clock = lambda: datetime(2026, 10, 18, 18, tzinfo=UTC)
    ready.record_stock(100, {"Курица": 2, "Лаваш": 60, "Соус": 0})
    with pytest.raises(UserError, match="сегодняшний"):
        ready.recommend(100)


def test_more_than_three_members_are_rejected(connected):
    info = connected.info(100)
    for user_id in (101, 102):
        _, code = connected.create_invite(point_id=1)
        connected.join(user_id, code)
    _, code = connected.create_invite(point_id=1)
    with pytest.raises(UserError, match="3 аккаунта"):
        connected.join(103, code)
    assert info["name"] == connected.info(102)["name"]
