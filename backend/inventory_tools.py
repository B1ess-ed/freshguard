from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable


STATUS_LABELS = {
    "fresh": "状态良好",
    "soon": "临期",
    "expired": "已过期",
    "low": "余量不足",
}

RECIPE_MARKERS = ("菜谱", "做什么", "怎么做", "如何做", "做一道", "推荐一道", "吃什么", "烹饪")
INVENTORY_MARKERS = (
    "库存",
    "食材",
    "还有",
    "有没",
    "有没有",
    "剩余",
    "还剩",
    "余量",
    "数量",
    "几种",
    "保质期",
    "过期",
    "临期",
    "快没",
    "不足",
    "有啥",
    "都有",
    "快用完",
)
GLOBAL_MARKERS = ("哪些", "什么", "清单", "全部", "所有", "几种", "总数", "一共")
PRONOUN_MARKERS = ("它", "这个", "那个", "其中", "这些", "那些")
GENERIC_NAME_FRAGMENTS = {"库存", "食材", "食品", "东西", "查询", "测试", "测试品"}


@dataclass(frozen=True)
class InventoryRoute:
    answer: str


def route_inventory_query(
    message: str,
    history: Iterable[dict[str, str]],
    inventory: list[dict],
) -> InventoryRoute | None:
    """Return a deterministic inventory answer, or None for a model-routed query."""
    text = normalize(message)
    if not text or any(marker in text for marker in RECIPE_MARKERS):
        return None

    categories = sorted({item["category"] for item in inventory}, key=len, reverse=True)
    status_filter = detect_status(text)
    intent = detect_intent(text, status_filter)
    entities, ambiguous = match_entities(text, inventory)
    category = next((value for value in categories if value in text), None)

    inventory_like = bool(
        intent
        or status_filter
        or entities
        or ambiguous
        or category
        or any(marker in text for marker in INVENTORY_MARKERS)
    )
    if not inventory_like:
        return None

    if not entities and not ambiguous and should_use_context(text, intent, status_filter):
        entities, ambiguous, historic_category = context_from_history(history, inventory, categories)
        category = category or historic_category

    if ambiguous:
        names = "、".join(item["name"] for item in ambiguous)
        return InventoryRoute(f"找到多个可能的食材：{names}。请说出完整名称。")

    if entities:
        return InventoryRoute(answer_for_items(entities, intent or "existence"))

    if status_filter:
        items = [item for item in inventory if item["status"] == status_filter]
        if category:
            items = [item for item in items if item["category"] == category]
        return InventoryRoute(answer_for_status(items, status_filter, category))

    if category:
        items = [item for item in inventory if item["category"] == category]
        return InventoryRoute(answer_for_listing(items, category))

    if intent == "summary":
        return InventoryRoute(f"当前共有 {len(inventory)} 种食材。")
    if intent == "list":
        return InventoryRoute(answer_for_listing(inventory))

    unknown = extract_requested_name(text)
    if unknown:
        return InventoryRoute(f"库存中没有找到“{unknown}”。")
    return InventoryRoute("请告诉我具体食材名称，或说明要查询全部、临期、过期还是低库存食材。")


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def detect_status(text: str) -> str | None:
    if any(value in text for value in ("快过期", "即将过期", "临期")):
        return "soon"
    if any(value in text for value in ("什么时候过期", "哪天过期", "保质期", "到期时间")):
        return None
    if "过期" in text:
        return "expired"
    if any(value in text for value in ("低库存", "余量不足", "库存不足", "快没", "不多", "快用完")):
        return "low"
    if any(value in text for value in ("状态良好", "新鲜的")):
        return "fresh"
    return None


def detect_intent(text: str, status_filter: str | None) -> str | None:
    if any(value in text for value in ("保质期", "什么时候过期", "哪天过期", "到期时间")):
        return "expiry"
    if any(value in text for value in ("多少种", "几种", "库存总数", "食材总数", "一共多少")):
        return "summary"
    if any(value in text for value in ("还剩多少", "剩余多少", "余量", "数量", "有多少")):
        return "quantity"
    if status_filter:
        return "status"
    if any(value in text for value in (
        "有哪些", "有什么", "有啥", "都有啥", "都有什么", "都有哪些",
        "库存清单", "全部库存", "全部食材", "所有食材",
    )):
        return "list"
    if re.search(r"(?:还有|有|有没有|有没).+(?:吗|么|？|\?)?$", text):
        return "existence"
    if "状态" in text or "新鲜吗" in text or "过期吗" in text:
        return "status"
    return None


def match_entities(text: str, inventory: list[dict]) -> tuple[list[dict], list[dict]]:
    exact = [item for item in inventory if normalize(item["name"]) in text]
    if exact:
        return exact, []

    scored: list[tuple[int, dict]] = []
    for item in inventory:
        name = normalize(item["name"])
        best = 0
        for start in range(len(name)):
            for end in range(start + 2, len(name) + 1):
                fragment = name[start:end]
                if fragment not in GENERIC_NAME_FRAGMENTS and fragment in text:
                    best = max(best, len(fragment))
        if best:
            scored.append((best, item))
    if not scored:
        return [], []
    top_score = max(score for score, _ in scored)
    matches = [item for score, item in scored if score == top_score]
    return (matches, []) if len(matches) == 1 else ([], matches)


def should_use_context(text: str, intent: str | None, status_filter: str | None) -> bool:
    if any(marker in text for marker in PRONOUN_MARKERS):
        return True
    return bool(intent in {"quantity", "expiry", "existence", "status"} and not status_filter and not any(
        marker in text for marker in GLOBAL_MARKERS
    ))


def context_from_history(
    history: Iterable[dict[str, str]],
    inventory: list[dict],
    categories: list[str],
) -> tuple[list[dict], list[dict], str | None]:
    for message in reversed(list(history)[-10:]):
        if message.get("role") != "user":
            continue
        text = normalize(message.get("content", ""))
        entities, ambiguous = match_entities(text, inventory)
        category = next((value for value in categories if value in text), None)
        if entities or ambiguous or category:
            return entities, ambiguous, category
    return [], [], None


def answer_for_items(items: list[dict], intent: str) -> str:
    if intent == "existence":
        return "\n".join(
            (
                f"有，{item['name']}当前有 {format_quantity(item)}，{STATUS_LABELS[item['status']]}。"
                if item["quantity"] > 0
                else f"库存记录中有{item['name']}，但当前数量为 0{item['unit']}。"
            )
            for item in items
        )
    if intent == "quantity":
        return "\n".join(
            f"{item['name']}当前有 {format_quantity(item)}，{STATUS_LABELS[item['status']]}。"
            for item in items
        )
    if intent == "expiry":
        return "\n".join(expiry_line(item) for item in items)
    if intent == "status":
        return "\n".join(
            f"{item['name']}当前{STATUS_LABELS[item['status']]}，余量 {format_quantity(item)}"
            f"{expiry_suffix(item)}。" for item in items
        )
    return "\n".join(f"{item['name']}：{format_quantity(item)}，{STATUS_LABELS[item['status']]}。" for item in items)


def answer_for_status(items: list[dict], status_value: str, category: str | None) -> str:
    label = STATUS_LABELS[status_value]
    scope = f"{category}中" if category else "当前"
    if not items:
        return f"{scope}没有{label}的食材。"
    details = "；".join(f"{item['name']}（{format_quantity(item)}{expiry_suffix(item)}）" for item in items)
    return f"{scope}{label}的食材有 {len(items)} 种：{details}。"


def answer_for_listing(items: list[dict], category: str | None = None) -> str:
    scope = category or "库存"
    if not items:
        return f"{scope}中目前没有食材。" if category else "当前库存为空。"
    details = "；".join(f"{item['name']}（{format_quantity(item)}，{STATUS_LABELS[item['status']]}）" for item in items)
    return f"{scope}共有 {len(items)} 种：{details}。"


def extract_requested_name(text: str) -> str | None:
    patterns = (
        r"(?:还有|有没有|有没|有)(.+?)(?:吗|么|？|\?)?$",
        r"(.+?)(?:还剩多少|剩余多少|有多少|的余量|的数量|什么时候过期|保质期)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip("的库存里冰箱中现在当前").rstrip("吗么")
            if value and not any(marker in value for marker in GLOBAL_MARKERS):
                return value
    return None


def format_quantity(item: dict) -> str:
    return f"{item['quantity']:g}{item['unit']}"


def expiry_line(item: dict) -> str:
    expiry = item["expiration_date"]
    if not expiry:
        return f"{item['name']}未设置保质期。"
    days = (date.fromisoformat(expiry) - date.today()).days
    if days < 0:
        timing = f"已过期 {-days} 天"
    elif days == 0:
        timing = "今天到期"
    else:
        timing = f"还有 {days} 天到期"
    return f"{item['name']}的保质期是 {expiry}，{timing}。"


def expiry_suffix(item: dict) -> str:
    return f"，保质期 {item['expiration_date']}" if item["expiration_date"] else "，未设置保质期"
