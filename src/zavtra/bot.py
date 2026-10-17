"""Telegram — тонкий интерфейс к общим прикладным сценариям."""

import asyncio
import io
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from zavtra.domain import UserError, display
from zavtra.imports import MAX_BYTES
from zavtra.presentation import order_text, recommendation_text
from zavtra.samples import INGREDIENTS, RECIPES, sales_csv

HELP = """Завтра — закупка для вашей точки общепита.

/demo — заполнить пустую точку учебными данными
/ingredients — загрузить ингредиенты и цены
/recipes — загрузить техкарты
/sales — загрузить продажи из CSV/XLSX
/templates — получить примеры файлов
/stock — ввести вечерние остатки
/recommend — рассчитать закупку на завтра
/forecast — посмотреть прогноз порций
/order — посмотреть последний подтверждённый заказ
/cancel — отменить текущий ввод

Для первого подключения: /start КОД
"""
MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="/stock"), KeyboardButton(text="/recommend")],
        [KeyboardButton(text="/order"), KeyboardButton(text="/help")],
    ],
    resize_keyboard=True,
)


class Input(StatesGroup):
    file = State()
    stock = State()
    quantity = State()


class UserErrors(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except UserError as exc:
            if isinstance(event, CallbackQuery):
                await event.answer(str(exc)[:180], show_alert=True)
            else:
                await event.answer(str(exc)[:3500])
        except Exception as exc:
            # Не пишем SQL-параметры, строки продаж, приглашения и токен в лог.
            logging.getLogger(__name__).error("Ошибка обработки: %s", type(exc).__name__)
            text = "Не удалось выполнить действие. Попробуйте ещё раз или обратитесь к команде проекта."
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                await event.answer(text)


def keyboard(run: dict):
    if run["status"] != "draft":
        return None
    rows = [
        [
            InlineKeyboardButton(
                text=f"Изменить: {i['name']}", callback_data=f"edit:{run['id']}:{i['id']}"
            )
        ]
        for i in run["items"]
    ]
    rows.append(
        [InlineKeyboardButton(text="Подтвердить заказ", callback_data=f"confirm:{run['id']}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_dispatcher(service) -> Dispatcher:
    router = Router()
    # Данные точки показываются только в личной переписке с ботом.
    router.message.filter(F.chat.type == "private")
    router.callback_query.filter(F.message.chat.type == "private")
    router.message.outer_middleware(UserErrors())
    router.callback_query.outer_middleware(UserErrors())

    async def invoke(method, *args):
        return await asyncio.to_thread(method, *args)

    async def show_run(message, run):
        text = recommendation_text(run)
        chunks = [text[i : i + 3500] for i in range(0, len(text), 3500)]
        for chunk in chunks[:-1]:
            await message.answer(chunk)
        await message.answer(chunks[-1], reply_markup=keyboard(run))

    @router.message(CommandStart())
    async def start(message: Message, command: CommandObject, state: FSMContext):
        await state.clear()
        if command.args:
            name = await invoke(service.join, message.from_user.id, command.args.strip())
            await message.answer(f"Подключена точка «{name}».\n\n{HELP}", reply_markup=MENU)
        else:
            await message.answer(HELP, reply_markup=MENU)

    @router.message(Command("help"))
    async def help_command(message: Message):
        await message.answer(HELP, reply_markup=MENU)

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext):
        await state.clear()
        await message.answer("Ввод отменён. Сохранённые данные доступны в меню.", reply_markup=MENU)

    @router.message(Command("demo"))
    async def demo(message: Message, state: FSMContext):
        await state.clear()
        await invoke(service.load_demo, message.from_user.id)
        await message.answer(
            "Добавлены учебные продажи за 84 дня, 3 ингредиента и 2 техкарты.\nВведите вечерние остатки: /stock."
        )

    @router.message(Command("ingredients", "recipes", "sales"))
    async def upload_prompt(message: Message, command: CommandObject, state: FSMContext):
        await invoke(service.info, message.from_user.id)
        prompts = {
            "ingredients": "Отправьте CSV/XLSX с колонками name, unit, price, co, cu, step.\nИнгредиенты с теми же названиями будут обновлены.",
            "recipes": "Отправьте CSV/XLSX с колонками dish, ingredient, amount.\nФайл заменит все техкарты точки.",
            "sales": "Отправьте CSV/XLSX с колонками дата, блюдо, количество (или date, dish, quantity).\nПродажи за даты файла будут заменены. Для прогноза нужно минимум 28 дней и продажи за сегодня.",
        }
        await state.set_state(Input.file)
        await state.set_data({"kind": command.command})
        await message.answer(prompts[command.command] + "\nПримеры: /templates. Отмена: /cancel.")

    @router.message(Command("templates"))
    async def templates(message: Message):
        info = await invoke(service.info, message.from_user.id)
        for filename, text in [
            ("ingredients.csv", INGREDIENTS),
            ("recipes.csv", RECIPES),
            ("sales.csv", sales_csv(info["today"])),
        ]:
            await message.answer_document(
                BufferedInputFile(text.encode("utf-8-sig"), filename=filename)
            )

    @router.message(Command("stock"))
    async def stock_prompt(message: Message, state: FSMContext):
        info = await invoke(service.info, message.from_user.id)
        if not info["ingredients"]:
            raise UserError("Сначала загрузите ингредиенты: /ingredients или /demo.")
        await state.set_state(Input.stock)
        await state.set_data({})
        lines = [
            f"Остатки на вечер {info['today']} ({info['timezone']}).",
            "Введите количества одним сообщением, например:",
        ]
        lines.extend(
            f"{i['name']}={display(info['stock'].get(i['id'], '0'))}" for i in info["ingredients"]
        )
        lines.append(
            "\nКоличество — в единице ингредиента. Можно вводить по одной строке. /cancel — отмена."
        )
        await message.answer("\n".join(lines))

    @router.message(Command("recommend"))
    async def recommend(message: Message, state: FSMContext):
        await state.clear()
        run = await invoke(service.recommend, message.from_user.id)
        await show_run(message, run)

    @router.message(Command("forecast"))
    async def forecast_command(message: Message):
        run = await invoke(service.latest, message.from_user.id)
        if run["status"] == "stale":
            raise UserError("Прогноз неактуален. Получите новую рекомендацию: /recommend.")
        lines = [f"Прогноз порций на {run['target_date']}"]
        lines.extend(f"{dish}: {display(value)}" for dish, value in run["forecast"].items())
        text = "\n".join(lines)
        for offset in range(0, len(text), 3500):
            await message.answer(text[offset : offset + 3500])

    @router.message(Command("order"))
    async def order_command(message: Message):
        await message.answer(order_text(await invoke(service.order, message.from_user.id)))

    @router.callback_query(F.data.startswith("edit:"))
    async def edit_prompt(callback: CallbackQuery, state: FSMContext):
        _, run_id, ingredient_id = callback.data.split(":")
        run = await invoke(service.latest, callback.from_user.id)
        if run["id"] != int(run_id) or run["status"] != "draft":
            raise UserError("Рекомендация неактуальна. Запросите /recommend.")
        item = next((i for i in run["items"] if i["id"] == int(ingredient_id)), None)
        if not item:
            raise UserError("Ингредиент не найден.")
        await state.set_state(Input.quantity)
        await state.set_data({"run_id": int(run_id), "ingredient_id": int(ingredient_id)})
        await callback.answer()
        await callback.message.answer(
            f"{item['name']}: введите количество к заказу в {item['unit']} (не меньше 0)."
        )

    @router.callback_query(F.data.startswith("confirm:"))
    async def confirm(callback: CallbackQuery, state: FSMContext):
        run_id = int(callback.data.split(":")[1])
        order = await invoke(service.confirm, callback.from_user.id, run_id)
        await state.clear()
        await callback.answer("Заказ сохранён")
        await callback.message.answer(order_text(order))

    @router.message(Input.file, F.document)
    async def upload(message: Message, state: FSMContext, bot: Bot):
        await invoke(service.info, message.from_user.id)
        if message.document.file_size and message.document.file_size > MAX_BYTES:
            raise UserError("Файл больше 5 МБ.")
        data = await state.get_data()
        buffer = io.BytesIO()
        await bot.download(message.document, destination=buffer)
        methods = {
            "ingredients": service.import_ingredients,
            "recipes": service.import_recipes,
            "sales": service.import_sales,
        }
        result = await invoke(
            methods[data["kind"]],
            message.from_user.id,
            buffer.getvalue(),
            message.document.file_name or "file",
        )
        await state.clear()
        if isinstance(result, dict):
            await message.answer(
                f"Сохранено: {result['rows']} строк, {result['days']} дней продаж."
            )
        else:
            await message.answer(f"Сохранено строк: {result}.")

    @router.message(Input.stock, F.text)
    async def stock_input(message: Message, state: FSMContext):
        values = {}
        for line in message.text.splitlines():
            if not line.strip():
                continue
            key, sep, value = line.partition("=")
            if not sep or not key.strip() or key.strip() in values:
                raise UserError("По одной неповторяющейся строке на ингредиент: Курица=2.")
            values[key.strip()] = value.strip()
        await invoke(service.record_stock, message.from_user.id, values)
        info = await invoke(service.info, message.from_user.id)
        missing = [i["name"] for i in info["ingredients"] if i["id"] not in info["stock"]]
        await message.answer(
            "Остатки сохранены." + (" Остались: " + ", ".join(missing) if missing else "")
        )
        if not missing:
            await state.clear()
            await show_run(message, await invoke(service.recommend, message.from_user.id))

    @router.message(Input.quantity, F.text)
    async def quantity_input(message: Message, state: FSMContext):
        data = await state.get_data()
        run = await invoke(
            service.edit, message.from_user.id, data["run_id"], data["ingredient_id"], message.text
        )
        await state.clear()
        await show_run(message, run)

    @router.message()
    async def fallback(message: Message):
        await message.answer("Выберите действие: /help. Чтобы отменить текущий ввод: /cancel.")

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def run_bot(service, token: str):
    if not token:
        raise UserError("Добавьте BOT_TOKEN в .env. Демонстрация без токена: python -m zavtra demo")
    bot = Bot(token=token)
    async with bot:
        await bot.set_my_commands(
            [
                BotCommand(command=c, description=d)
                for c, d in [
                    ("stock", "Вечерние остатки"),
                    ("recommend", "Закупка на завтра"),
                    ("order", "Последний заказ"),
                    ("sales", "Загрузить продажи"),
                    ("help", "Помощь"),
                ]
            ]
        )
        await create_dispatcher(service).start_polling(bot)
