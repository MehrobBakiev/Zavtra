import argparse
import asyncio
import os
import secrets
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from zavtra.database import Database
from zavtra.domain import UserError
from zavtra.presentation import order_text, recommendation_text
from zavtra.samples import INGREDIENTS, RECIPES, sales_csv
from zavtra.service import Service


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Завтра — бот закупок для малого общепита")
    parser.add_argument(
        "--database-url", default=os.getenv("DATABASE_URL", "sqlite:///data/zavtra.db")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Создать первую схему базы")
    commands.add_parser("bot", help="Запустить Telegram-бота")
    commands.add_parser("demo", help="Пройти сценарий на синтетических данных без Telegram")
    invite = commands.add_parser("invite", help="Создать одноразовый код подключения на 24 часа")
    invite.add_argument("--name", default="Пилотная точка")
    invite.add_argument("--timezone", default="Europe/Moscow")
    invite.add_argument("--point-id", type=int)
    samples = commands.add_parser("samples", help="Создать CSV-примеры со свежими датами")
    samples.add_argument("--output-dir", default="data/samples")
    args = parser.parse_args()
    try:
        if args.command == "samples":
            output = Path(args.output_dir)
            output.mkdir(parents=True, exist_ok=True)
            for name, text in [
                ("ingredients.csv", INGREDIENTS),
                ("recipes.csv", RECIPES),
                ("sales.csv", sales_csv(date.today())),
            ]:
                (output / name).write_text(text, encoding="utf-8-sig")
            print(f"Примеры сохранены: {output.resolve()}")
            return
        database = Database(args.database_url)
        database.initialize()
        service = Service(database)
        if args.command == "init":
            print("База готова.")
        elif args.command == "invite":
            point_id, code = service.create_invite(args.name, args.timezone, args.point_id)
            print(f"Точка №{point_id}. Одноразовый код действует 24 часа.\n/start {code}")
        elif args.command == "bot":
            from zavtra.bot import run_bot

            asyncio.run(run_bot(service, os.getenv("BOT_TOKEN", "")))
        elif args.command == "demo":
            user_id = 10**12 + secrets.randbelow(10**12)
            _, code = service.create_invite("Демонстрационная шаурмичная")
            service.join(user_id, code)
            service.load_demo(user_id)
            service.record_stock(user_id, {"Курица": "2", "Лаваш": "60", "Соус": "0.5"})
            run = service.recommend(user_id)
            print("СИНТЕТИЧЕСКИЕ ДАННЫЕ · демонстрация без Telegram\n")
            print(recommendation_text(run))
            print("\n" + order_text(service.confirm(user_id, run["id"])))
    except UserError as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
