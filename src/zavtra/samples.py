"""Синтетические примеры: не данные реального заведения."""

import csv
import io
from datetime import date, timedelta

INGREDIENTS = """name,unit,price,co,cu,step
Курица,кг,400,400,1200,1
Лаваш,шт,15,15,100,10
Соус,л,200,200,600,0.5
"""
RECIPES = """dish,ingredient,amount
Шаурма,Курица,0.12
Шаурма,Лаваш,1
Шаурма,Соус,0.03
Шаурма XL,Курица,0.18
Шаурма XL,Лаваш,1
Шаурма XL,Соус,0.04
"""


def sales_csv(today: date, days: int = 84) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "dish", "quantity"])
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        portions = [30, 30, 35, 40, 60, 55, 40][day.weekday()]
        writer.writerow([day.isoformat(), "Шаурма", portions])
        writer.writerow([day.isoformat(), "Шаурма XL", portions // 4])
    return output.getvalue()
