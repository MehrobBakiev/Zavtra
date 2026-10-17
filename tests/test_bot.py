from datetime import UTC, datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, GetMe, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from zavtra.bot import create_dispatcher


class FakeTelegram(BaseSession):
    """Исполняем реальные маршруты aiogram без запросов в Telegram."""

    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, GetMe):
            return User(id=123456, is_bot=True, first_name="Завтра", username="zavtra_test_bot")
        if isinstance(method, AnswerCallbackQuery):
            return True
        if isinstance(method, SendMessage):
            return Message(
                message_id=len(self.sent),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        raise AssertionError(type(method).__name__)


async def test_telegram_start_demo_stock_edit_and_confirm(service):
    _, code = service.create_invite("Тест бота")
    session = FakeTelegram()
    bot = Bot("123456:TEST_TOKEN", session=session)
    dispatcher = create_dispatcher(service)
    user = User(id=100, is_bot=False, first_name="Тест")
    counter = 0

    async def send(text):
        nonlocal counter
        counter += 1
        message = Message(
            message_id=counter,
            date=datetime.now(UTC),
            chat=Chat(id=100, type="private"),
            from_user=user,
            text=text,
        )
        await dispatcher.feed_update(bot, Update(update_id=counter, message=message))

    async def click(data):
        nonlocal counter
        counter += 1
        message = Message(
            message_id=counter,
            date=datetime.now(UTC),
            chat=Chat(id=100, type="private"),
            from_user=User(id=123456, is_bot=True, first_name="Завтра"),
            text="Рекомендация",
        )
        callback = CallbackQuery(
            id=str(counter), from_user=user, chat_instance="test", message=message, data=data
        )
        await dispatcher.feed_update(bot, Update(update_id=counter, callback_query=callback))

    try:
        await send(f"/start {code}")
        await send("/demo")
        await send("/stock")
        await send("Курица=2\nЛаваш=60\nСоус=0.5")
        run = service.latest(100)
        await click(f"edit:{run['id']}:{run['items'][0]['id']}")
        await send("8")
        await click(f"confirm:{run['id']}")
        await click(f"confirm:{run['id']}")
        assert service.order(100)["items"][0]["final"] == "8"
        assert sum("подтверждён." in (getattr(m, "text", "") or "") for m in session.sent) == 2
        assert not any("Не удалось" in (getattr(m, "text", "") or "") for m in session.sent)
    finally:
        await dispatcher.storage.close()
        await bot.session.close()


async def test_group_chat_does_not_receive_point_data(ready):
    session = FakeTelegram()
    bot = Bot("123456:TEST_TOKEN", session=session)
    dispatcher = create_dispatcher(ready)
    try:
        message = Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=-100, type="group"),
            from_user=User(id=100, is_bot=False, first_name="Тест"),
            text="/stock",
        )
        await dispatcher.feed_update(bot, Update(update_id=1, message=message))
        assert session.sent == []
    finally:
        await dispatcher.storage.close()
        await bot.session.close()
