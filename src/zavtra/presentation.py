"""Текстовые представления для Telegram и демонстрации в терминале."""

from zavtra.domain import display


def recommendation_text(run: dict) -> str:
    lines = [f"Закупка на {run['target_date']}"]
    if run["status"] == "stale":
        lines.append("Эта рекомендация неактуальна. Получите новую: /recommend.")
    for item in run["items"]:
        quantity = "не рассчитано" if item["final"] is None else display(item["final"])
        lines.extend(
            [
                f"\n{item['name']}: к заказу {quantity} {item['unit']}",
                f"Потребность {display(item['demand'])}; остаток {display(item['stock'])} {item['unit']}",
            ]
        )
        if item["note"]:
            lines.append(item["note"])
        if item["final"] != item["recommended"]:
            original = (
                "не рассчитано" if item["recommended"] is None else display(item["recommended"])
            )
            lines.append(f"Рекомендовано: {original}; количество изменено вручную.")
    lines.extend("\n" + text for text in run["warnings"])
    return "\n".join(lines)


def order_text(order: dict) -> str:
    lines = [f"Заказ №{order['id']} на {order['target_date']} подтверждён."]
    lines.extend(f"{i['name']}: {display(i['final'])} {i['unit']}" for i in order["items"])
    lines.append("\nЭтот список можно отправить поставщику.")
    return "\n".join(lines)
