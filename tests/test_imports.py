import io
from datetime import date, datetime

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select

from zavtra.database import SaleRecord
from zavtra.domain import UserError
from zavtra.imports import MAX_BYTES, sales_file
from zavtra.samples import sales_csv


def xlsx_bytes(rows):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def test_csv_russian_headers_and_xlsx_have_same_result():
    today = date(2026, 10, 17)
    csv = "дата;блюдо;количество\n17.10.2026;Шаурма;5\n".encode("utf-8-sig")
    xlsx = xlsx_bytes([["дата", "блюдо", "количество"], [datetime(2026, 10, 17), "Шаурма", 5]])
    assert sales_file(csv, "sales.csv", today) == sales_file(xlsx, "sales.xlsx", today)


def test_import_replaces_days_and_invalid_file_rolls_back(connected):
    content = sales_csv(date(2026, 10, 17)).encode()
    connected.import_sales(100, content, "sales.csv")
    connected.import_sales(100, content, "sales.csv")
    with connected.db.session() as session:
        assert session.scalar(select(func.count()).select_from(SaleRecord)) == 168
        before = session.scalar(select(func.sum(SaleRecord.quantity)))
    malformed = b"date,dish,quantity\n2026-10-17,test,999\n2026-10-17,test,-2\n"
    with pytest.raises(UserError, match="Строка 3"):
        connected.import_sales(100, malformed, "sales.csv")
    with connected.db.session() as session:
        assert session.scalar(select(func.sum(SaleRecord.quantity))) == before
    connected.import_sales(100, b"date,dish,quantity\n2026-10-17,test,5\n", "sales.csv")
    with connected.db.session() as session:
        today = session.scalars(
            select(SaleRecord).where(SaleRecord.day == date(2026, 10, 17))
        ).all()
        assert [(r.dish, r.quantity) for r in today] == [("test", 5)]


@pytest.mark.parametrize(
    "filename,content",
    [
        ("sales.pdf", b"fake pdf"),
        ("sales.xlsx", b"not a zip"),
        ("sales.csv", b"x" * (MAX_BYTES + 1)),
        ("sales.csv", b"date,dish,quantity\n2026-10-18,test,1\n"),
    ],
)
def test_rejects_invalid_files(filename, content):
    with pytest.raises(UserError):
        sales_file(content, filename, date(2026, 10, 17))


def test_zero_co_in_excel_is_rejected(connected):
    content = xlsx_bytes([["name", "unit", "price", "co"], ["Курица", "кг", 400, 0]])
    with pytest.raises(UserError, match="Co"):
        connected.import_ingredients(100, content, "ingredients.xlsx")
