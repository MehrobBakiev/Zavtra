from datetime import UTC, datetime

import pytest

from zavtra.database import Database
from zavtra.service import Service


@pytest.fixture
def service(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    database.initialize()
    app = Service(database, lambda: datetime(2026, 10, 17, 18, tzinfo=UTC))
    yield app
    database.engine.dispose()


@pytest.fixture
def connected(service):
    _, code = service.create_invite("Тестовая точка")
    service.join(100, code)
    return service


@pytest.fixture
def ready(connected):
    connected.load_demo(100)
    connected.record_stock(100, {"Курица": 2, "Лаваш": 60, "Соус": "0.5"})
    return connected
