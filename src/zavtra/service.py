"""Прикладные сценарии, общие для бота и будущего API."""

import hashlib
import secrets
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select, update

from zavtra.database import (
    Database,
    Ingredient,
    Invite,
    Member,
    Order,
    Point,
    Recipe,
    Recommendation,
    SaleRecord,
    Stock,
)
from zavtra.domain import ALGORITHM_VERSION, UserError, calculate, number
from zavtra.imports import required, sales_file, table
from zavtra.samples import INGREDIENTS, RECIPES, sales_csv


class Service:
    def __init__(self, database: Database, clock: Callable[[], datetime] | None = None):
        self.db = database
        self.clock = clock or (lambda: datetime.now(UTC))

    def _today(self, point):
        return self.clock().astimezone(ZoneInfo(point.timezone)).date()

    def _point(self, session, telegram_id: int, *, lock: bool = False) -> Point:
        member = session.get(Member, telegram_id)
        if not member:
            raise UserError("Точка не подключена. Получите код и отправьте /start КОД.")
        if lock:
            # UPDATE берёт блокировку строки в PG и блокировку записи в SQLite.
            # Единый порядок блокировок сериализует изменения одной точки.
            session.execute(
                update(Point).where(Point.id == member.point_id).values(revision=Point.revision)
            )
        return session.get(Point, member.point_id, populate_existing=True)

    def _touch(self, session, point):
        point.revision += 1
        session.execute(
            update(Recommendation)
            .where(
                Recommendation.point_id == point.id,
                Recommendation.status == "draft",
            )
            .values(status="stale")
        )

    def create_invite(self, name="Пилотная точка", timezone="Europe/Moscow", point_id=None):
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            raise UserError("Неизвестный часовой пояс IANA.") from None
        if not name.strip() or len(name) > 100:
            raise UserError("Название точки: от 1 до 100 символов.")
        code = secrets.token_urlsafe(18)
        with self.db.session.begin() as session:
            if point_id is None:
                point = Point(name=name, timezone=timezone)
                session.add(point)
                session.flush()
                point_id = point.id
            elif not session.get(Point, point_id):
                raise UserError("Точка не найдена.")
            session.add(
                Invite(
                    code_hash=hashlib.sha256(code.encode()).hexdigest(),
                    point_id=point_id,
                    expires_at=int((self.clock() + timedelta(hours=24)).timestamp()),
                )
            )
        return point_id, code

    def join(self, telegram_id: int, code: str) -> str:
        with self.db.session.begin() as session:
            if member := session.get(Member, telegram_id):
                return session.get(Point, member.point_id).name
            digest = hashlib.sha256(code.encode()).hexdigest()
            invite = session.get(Invite, digest)
            if not invite:
                raise UserError("Код недействителен или просрочен.")
            session.execute(
                update(Point).where(Point.id == invite.point_id).values(revision=Point.revision)
            )
            # Проверяем повторно после ожидания блокировки точки.
            session.refresh(invite)
            if invite.consumed_by is not None or invite.expires_at <= int(self.clock().timestamp()):
                raise UserError("Код недействителен или просрочен.")
            count = session.scalar(
                select(func.count()).select_from(Member).where(Member.point_id == invite.point_id)
            )
            if count >= 3:
                raise UserError("К точке уже подключены 3 аккаунта.")
            session.add(Member(telegram_id=telegram_id, point_id=invite.point_id))
            invite.consumed_by = telegram_id
            return session.get(Point, invite.point_id).name

    def info(self, telegram_id):
        with self.db.session() as session:
            point = self._point(session, telegram_id)
            ingredients = session.scalars(
                select(Ingredient).where(Ingredient.point_id == point.id).order_by(Ingredient.id)
            ).all()
            today = self._today(point)
            stock = {
                r.ingredient_id: str(r.quantity)
                for r in session.scalars(
                    select(Stock).where(Stock.point_id == point.id, Stock.day == today)
                )
            }
            return {
                "name": point.name,
                "today": today,
                "timezone": point.timezone,
                "ingredients": [{"id": i.id, "name": i.name, "unit": i.unit} for i in ingredients],
                "stock": stock,
            }

    def import_ingredients(self, telegram_id, content: bytes, filename: str):
        rows = table(content, filename)
        required(rows, {"name", "unit", "price"})
        parsed = []
        names = set()
        for row in rows:
            try:
                name = str(row["name"] or "").strip()
                unit = str(row["unit"] or "").strip()
                if not name or len(name) > 100 or name in names:
                    raise UserError("Название ингредиента пустое, слишком длинное или повторяется.")
                if unit not in {"кг", "л", "шт"}:
                    raise UserError("Единица: кг, л или шт.")
                names.add(name)
                price = number(row["price"], "Цена", positive=True)
                parsed.append(
                    {
                        "name": name,
                        "unit": unit,
                        "price": price,
                        "co": number(
                            price if row.get("co") in (None, "") else row["co"], "Co", positive=True
                        ),
                        "cu": number(row["cu"], "Cu", positive=True)
                        if row.get("cu") not in (None, "")
                        else None,
                        "step": number(
                            1 if row.get("step") in (None, "") else row["step"],
                            "Кратность",
                            positive=True,
                        ),
                    }
                )
            except UserError as exc:
                raise UserError(f"Строка {row['_line']}: {exc}") from None
        with self.db.session.begin() as session:
            point = self._point(session, telegram_id, lock=True)
            existing = {
                i.name: i
                for i in session.scalars(select(Ingredient).where(Ingredient.point_id == point.id))
            }
            if len(set(existing) | names) > 10:
                raise UserError("Допускается от 1 до 10 ингредиентов.")
            for values in parsed:
                ingredient = existing.get(values["name"])
                if ingredient and ingredient.unit != values["unit"]:
                    raise UserError("Единицу существующего ингредиента менять нельзя.")
                if not ingredient:
                    ingredient = Ingredient(point_id=point.id)
                    session.add(ingredient)
                for key, value in values.items():
                    setattr(ingredient, key, value)
            self._touch(session, point)
        return len(parsed)

    def import_recipes(self, telegram_id, content, filename):
        rows = table(content, filename)
        required(rows, {"dish", "ingredient", "amount"})
        with self.db.session.begin() as session:
            point = self._point(session, telegram_id, lock=True)
            ingredients = {
                i.name: i.id
                for i in session.scalars(select(Ingredient).where(Ingredient.point_id == point.id))
            }
            parsed, keys = [], set()
            for row in rows:
                try:
                    dish = str(row["dish"] or "").strip()
                    ingredient = str(row["ingredient"] or "").strip()
                    if not dish or len(dish) > 100:
                        raise UserError("Название блюда: от 1 до 100 символов.")
                    if ingredient not in ingredients:
                        raise UserError(f"Неизвестный ингредиент: {ingredient}.")
                    key = (dish, ingredient)
                    if key in keys:
                        raise UserError("Пара блюдо/ингредиент повторяется.")
                    keys.add(key)
                    parsed.append(
                        Recipe(
                            point_id=point.id,
                            dish=dish,
                            ingredient_id=ingredients[ingredient],
                            amount=number(row["amount"], "Норма", positive=True),
                        )
                    )
                except UserError as exc:
                    raise UserError(f"Строка {row['_line']}: {exc}") from None
            if len({p.dish for p in parsed}) > 100:
                raise UserError("Максимум 100 блюд.")
            session.execute(delete(Recipe).where(Recipe.point_id == point.id))
            session.add_all(parsed)
            self._touch(session, point)
        return len(parsed)

    def import_sales(self, telegram_id, content, filename):
        with self.db.session.begin() as session:
            point = self._point(session, telegram_id, lock=True)
            rows = sales_file(content, filename, self._today(point))
            grouped = defaultdict(int)
            for row in rows:
                grouped[row.day, row.dish] += row.quantity
            days = {r.day for r in rows}
            session.execute(
                delete(SaleRecord).where(SaleRecord.point_id == point.id, SaleRecord.day.in_(days))
            )
            session.add_all(
                [
                    SaleRecord(point_id=point.id, day=day, dish=dish, quantity=q)
                    for (day, dish), q in grouped.items()
                ]
            )
            self._touch(session, point)
        return {"rows": len(rows), "days": len(days)}

    def load_demo(self, telegram_id):
        with self.db.session() as session:
            point = self._point(session, telegram_id)
            exists = session.scalar(
                select(func.count()).select_from(Ingredient).where(Ingredient.point_id == point.id)
            )
            sales = session.scalar(
                select(func.count()).select_from(SaleRecord).where(SaleRecord.point_id == point.id)
            )
            if exists or sales:
                raise UserError("Демоданные загружаются только в пустую точку.")
            today = self._today(point)
        self.import_ingredients(telegram_id, INGREDIENTS.encode(), "ingredients.csv")
        self.import_recipes(telegram_id, RECIPES.encode(), "recipes.csv")
        self.import_sales(telegram_id, sales_csv(today).encode(), "sales.csv")

    def record_stock(self, telegram_id, values: dict[str, object]):
        if not values:
            raise UserError("Введите остатки в формате Курица=2, по одному ингредиенту в строке.")
        with self.db.session.begin() as session:
            point = self._point(session, telegram_id, lock=True)
            today = self._today(point)
            if session.scalar(
                select(Order.id).where(
                    Order.point_id == point.id, Order.target_date == today + timedelta(days=1)
                )
            ):
                raise UserError("Заказ на завтра уже подтверждён.")
            ingredients = session.scalars(
                select(Ingredient).where(Ingredient.point_id == point.id)
            ).all()
            lookup = {i.name: i.id for i in ingredients} | {str(i.id): i.id for i in ingredients}
            parsed = {}
            for key, raw in values.items():
                if key not in lookup:
                    raise UserError(f"Неизвестный ингредиент: {key}.")
                parsed[lookup[key]] = number(raw, key)
            for ingredient_id, quantity in parsed.items():
                stock = session.get(Stock, (point.id, today, ingredient_id))
                if stock:
                    stock.quantity = quantity
                else:
                    session.add(
                        Stock(
                            point_id=point.id,
                            day=today,
                            ingredient_id=ingredient_id,
                            quantity=quantity,
                        )
                    )
            self._touch(session, point)

    def recommend(self, telegram_id):
        with self.db.session.begin() as session:
            point = self._point(session, telegram_id, lock=True)
            today = self._today(point)
            target = today + timedelta(days=1)
            if session.scalar(
                select(Order.id).where(Order.point_id == point.id, Order.target_date == target)
            ):
                raise UserError("Заказ на завтра уже подтверждён. Посмотреть: /order.")
            ingredients = session.scalars(
                select(Ingredient).where(Ingredient.point_id == point.id).order_by(Ingredient.id)
            ).all()
            if not ingredients:
                raise UserError("Сначала загрузите ингредиенты: /ingredients или /demo.")
            stocks = {
                s.ingredient_id: str(s.quantity)
                for s in session.scalars(
                    select(Stock).where(Stock.point_id == point.id, Stock.day == today)
                )
            }
            if missing := [i.name for i in ingredients if i.id not in stocks]:
                raise UserError("Не введены остатки: " + ", ".join(missing))
            sales = session.scalars(
                select(SaleRecord).where(
                    SaleRecord.point_id == point.id,
                    SaleRecord.day <= today,
                    SaleRecord.day >= today - timedelta(days=364),
                )
            ).all()
            days = {s.day for s in sales}
            if len(days) < 28:
                raise UserError(f"Нужно минимум 28 загруженных дней продаж; сейчас {len(days)}.")
            if max(days) != today:
                raise UserError("Загрузите продажи за сегодняшний день, чтобы обновить прогноз.")
            recipes = session.scalars(select(Recipe).where(Recipe.point_id == point.id)).all()
            inputs = {
                "target_date": target.isoformat(),
                "revision": point.revision,
                "timezone": point.timezone,
                "ingredients": [
                    {
                        "id": i.id,
                        "name": i.name,
                        "unit": i.unit,
                        "price": str(i.price),
                        "co": str(i.co),
                        "cu": str(i.cu) if i.cu is not None else None,
                        "step": str(i.step),
                    }
                    for i in ingredients
                ],
                "recipes": [
                    {"dish": r.dish, "ingredient_id": r.ingredient_id, "amount": str(r.amount)}
                    for r in recipes
                ],
                "sales": [
                    {"day": s.day.isoformat(), "dish": s.dish, "quantity": s.quantity}
                    for s in sales
                ],
                "stocks": {str(k): v for k, v in stocks.items()},
            }
            result = calculate(inputs)
            session.execute(
                update(Recommendation)
                .where(Recommendation.point_id == point.id, Recommendation.status == "draft")
                .values(status="stale")
            )
            recommendation = Recommendation(
                point_id=point.id,
                target_date=target,
                input_revision=point.revision,
                status="draft",
                edits={},
                snapshot={
                    "algorithm": ALGORITHM_VERSION,
                    "input": inputs,
                    "result": result,
                    "created_at": self.clock().isoformat(),
                },
            )
            session.add(recommendation)
            session.flush()
            return self._view(recommendation)

    @staticmethod
    def _view(recommendation):
        result = recommendation.snapshot["result"]
        return {
            "id": recommendation.id,
            "target_date": recommendation.target_date.isoformat(),
            "status": recommendation.status,
            "forecast": result["forecast"],
            "warnings": result["warnings"],
            "items": [
                dict(i, final=recommendation.edits.get(str(i["id"]), i["recommended"]))
                for i in result["items"]
            ],
        }

    def latest(self, telegram_id):
        with self.db.session() as session:
            point = self._point(session, telegram_id)
            rec = session.scalar(
                select(Recommendation)
                .where(
                    Recommendation.point_id == point.id,
                    Recommendation.target_date == self._today(point) + timedelta(days=1),
                )
                .order_by(Recommendation.id.desc())
                .limit(1)
            )
            if not rec:
                raise UserError("Сначала получите рекомендацию: /recommend.")
            return self._view(rec)

    def _draft(self, session, point, recommendation_id):
        rec = session.scalar(
            select(Recommendation).where(
                Recommendation.id == recommendation_id, Recommendation.point_id == point.id
            )
        )
        if not rec:
            raise UserError("Рекомендация не найдена.")
        if (
            rec.status != "draft"
            or rec.input_revision != point.revision
            or rec.target_date != self._today(point) + timedelta(days=1)
        ):
            raise UserError(
                "Рекомендация неактуальна или заказ уже подтверждён. Запросите /recommend."
            )
        return rec

    def edit(self, telegram_id, recommendation_id, ingredient_id, quantity):
        value = number(quantity)
        with self.db.session.begin() as session:
            point = self._point(session, telegram_id, lock=True)
            rec = self._draft(session, point, recommendation_id)
            if ingredient_id not in {i["id"] for i in rec.snapshot["result"]["items"]}:
                raise UserError("Ингредиент не найден в рекомендации.")
            rec.edits = rec.edits | {str(ingredient_id): str(value)}
            return self._view(rec)

    def confirm(self, telegram_id, recommendation_id):
        with self.db.session.begin() as session:
            point = self._point(session, telegram_id, lock=True)
            existing = session.scalar(
                select(Order).where(
                    Order.point_id == point.id, Order.recommendation_id == recommendation_id
                )
            )
            if existing:
                return self._order_view(existing)
            rec = self._draft(session, point, recommendation_id)
            items = self._view(rec)["items"]
            if any(i["final"] is None for i in items):
                raise UserError("Укажите вручную количество для всех нерассчитанных позиций.")
            order = Order(
                point_id=point.id,
                target_date=rec.target_date,
                recommendation_id=rec.id,
                confirmed_by=telegram_id,
                confirmed_at=self.clock().isoformat(),
                items=items,
            )
            session.add(order)
            rec.status = "confirmed"
            session.flush()
            return self._order_view(order)

    @staticmethod
    def _order_view(order):
        return {
            "id": order.id,
            "target_date": order.target_date.isoformat(),
            "confirmed_at": order.confirmed_at,
            "items": order.items,
        }

    def order(self, telegram_id):
        with self.db.session() as session:
            point = self._point(session, telegram_id)
            order = session.scalar(
                select(Order).where(Order.point_id == point.id).order_by(Order.id.desc()).limit(1)
            )
            if not order:
                raise UserError("Подтверждённых заказов пока нет.")
            return self._order_view(order)
