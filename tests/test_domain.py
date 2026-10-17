from datetime import date
from decimal import Decimal as D

import pytest

from zavtra.domain import Sale, UserError, forecast, number, purchase
from zavtra.imports import sales_file
from zavtra.samples import sales_csv


def test_rtm_tc06_asymmetric_costs_and_pack_rounding():
    errors = [D("0")] * 12 + [D("0.1")] * 5
    high = purchase(D("6.6"), D(2), D(400), D(1200), D(1), errors)
    equal = purchase(D("6.6"), D(2), D(400), D(400), D(1), errors)
    low = purchase(D("6.6"), D(2), D(400), D(200), D(1), errors)
    assert D(high["cr"]) == D("0.75")
    assert D(high["target"]) == D("7.26")
    assert D(high["recommended"]) == 6
    assert D(equal["recommended"]) == D(low["recommended"]) == 5
    assert D(purchase(D(50), D(60), D(15), D(100), D(10), errors)["recommended"]) == 0


def test_short_backtest_has_explicit_default_buffer():
    result = purchase(D(10), D(0), D(1), D(2), D("0.5"), [D(0)] * 13)
    assert result["default_buffer"]
    assert D(result["recommended"]) == 11


def test_forecast_respects_weekday_and_does_not_read_future():
    today = date(2026, 10, 17)
    sales = sales_file(sales_csv(today).encode(), "sales.csv", today)
    tuesday = forecast(sales, date(2026, 10, 20))["Шаурма"]
    friday = forecast(sales, date(2026, 10, 23))["Шаурма"]
    assert friday >= D("1.5") * tuesday
    future = Sale(date(2026, 10, 21), "Шаурма", 100000)
    assert forecast(sales + [future], date(2026, 10, 20))["Шаурма"] == tuesday
    sales.append(Sale(date(2026, 8, 1), "Снято с меню", 5))
    assert forecast(sales, date(2026, 10, 18))["Снято с меню"] == 0


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "abc", "0.0000001"])
def test_invalid_quantities(value):
    with pytest.raises(UserError):
        number(value)


def test_decimal_rounding_does_not_add_an_extra_pack():
    exact = purchase(D("0.3"), D("0.1"), D(1), D(1), D("0.1"), [D(0)] * 14)
    assert D(exact["recommended"]) == D("0.2")
