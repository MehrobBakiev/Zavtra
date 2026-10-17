"""Чистые расчёты: без Telegram, ORM и сетевых запросов."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal, InvalidOperation

ALGORITHM_VERSION = "weekday-mean-v1/linear-quantile-v1"
ZERO = Decimal("0")


class UserError(ValueError):
    """Ошибка ввода или состояния, которую можно показать пользователю."""


def number(value: object, field: str = "Количество", *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise UserError(f"{field}: введите число.") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        sign = "больше нуля" if positive else "не меньше нуля"
        raise UserError(f"{field}: нужно конечное число {sign}.")
    if result > Decimal("1000000000") or result.as_tuple().exponent < -6:
        raise UserError(f"{field}: максимум 1 млрд и 6 знаков после запятой.")
    return result


def display(value: str | Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


@dataclass(frozen=True)
class Sale:
    day: date
    dish: str
    quantity: int


def forecast(sales: list[Sale], target: date) -> dict[str, Decimal]:
    """Средние по последним восьми таким дням недели; только известные дни."""
    history = [row for row in sales if row.day < target]
    days = sorted({row.day for row in history})
    dishes = sorted({row.dish for row in history})
    recent = target - timedelta(days=28)
    active = {row.dish for row in history if row.day >= recent and row.quantity > 0}
    values: dict[tuple[date, str], int] = defaultdict(int)
    for row in history:
        values[row.day, row.dish] += row.quantity
    same_weekday = [d for d in days if d.weekday() == target.weekday()][-8:]
    selected = same_weekday or days[-28:]
    return {
        dish: (
            sum((Decimal(values[d, dish]) for d in selected), ZERO) / len(selected)
            if dish in active and selected
            else ZERO
        )
        for dish in dishes
    }


def quantile(values: list[Decimal], probability: Decimal) -> Decimal:
    """Линейная интерполяция: индекс (n−1)*p, метод R-7."""
    if not values:
        raise ValueError("Для квантиля нужны наблюдения")
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def error_samples(sales: list[Sale], target: date, norms: dict[str, Decimal]) -> list[Decimal]:
    """Бэктест потребности; перед каждым прогнозом нужно 28 известных дней."""
    days = sorted({s.day for s in sales if s.day < target})
    result = []
    actual: dict[date, Decimal] = defaultdict(Decimal)
    for row in sales:
        actual[row.day] += Decimal(row.quantity) * norms.get(row.dish, ZERO)
    for day in days[-56:]:
        previous = [row for row in sales if row.day < day]
        if len({row.day for row in previous}) < 28:
            continue
        predicted = sum(
            (v * norms.get(dish, ZERO) for dish, v in forecast(previous, day).items()), ZERO
        )
        # Деление на ноль исключаем; такие дни не считаются валидными ошибками.
        if predicted > 0:
            result.append(actual[day] / predicted - 1)
    return result


def purchase(
    demand: Decimal,
    stock: Decimal,
    co: Decimal,
    cu: Decimal,
    step: Decimal,
    errors: list[Decimal],
) -> dict[str, str | bool]:
    cr = max(Decimal("0.50"), min(Decimal("0.95"), cu / (cu + co)))
    default = len(errors) < 14
    adjustment = Decimal("0.10") if default else quantile(errors, cr)
    target = max(ZERO, demand * (1 + adjustment))
    quantity = (max(ZERO, target - stock) / step).to_integral_value(rounding=ROUND_CEILING) * step
    return {
        "demand": str(demand),
        "stock": str(stock),
        "cr": str(cr),
        "error_quantile": str(adjustment),
        "target": str(target),
        "recommended": str(quantity),
        "default_buffer": default,
    }


def calculate(inputs: dict) -> dict:
    """Воспроизводимый результат по неизменяемому JSON-снимку входов."""
    target = date.fromisoformat(inputs["target_date"])
    sales = [Sale(date.fromisoformat(r["day"]), r["dish"], r["quantity"]) for r in inputs["sales"]]
    predictions = forecast(sales, target)
    covered = {r["dish"] for r in inputs["recipes"]}
    active = {r.dish for r in sales if r.day >= target - timedelta(days=28) and r.quantity > 0}
    warnings = []
    if missing := sorted(active - covered):
        warnings.append("Блюда без техкарты не учтены: " + ", ".join(missing))
    stocks = inputs["stocks"]
    items = []
    for ingredient in inputs["ingredients"]:
        item = {**ingredient, "stock": stocks[str(ingredient["id"])], "note": ""}
        norms = {
            r["dish"]: Decimal(r["amount"])
            for r in inputs["recipes"]
            if r["ingredient_id"] == ingredient["id"]
        }
        demand = sum((v * norms.get(dish, ZERO) for dish, v in predictions.items()), ZERO)
        item["demand"] = str(demand)
        if ingredient["cu"] is None:
            item.update(recommended=None, note="не рассчитано: не задан Cu")
        elif not norms:
            item.update(recommended="0", note="нет техкарт")
        else:
            errors = error_samples(sales, target, norms)
            item.update(
                purchase(
                    demand,
                    Decimal(item["stock"]),
                    Decimal(item["co"]),
                    Decimal(item["cu"]),
                    Decimal(item["step"]),
                    errors,
                )
            )
            item["errors"] = [str(v) for v in errors]
            if item["default_buffer"]:
                item["note"] = "запас по умолчанию +10%"
        items.append(item)
    return {
        "forecast": {k: str(v) for k, v in predictions.items()},
        "items": items,
        "warnings": warnings,
    }
