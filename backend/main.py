from __future__ import annotations

import os
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import Database
from .environment import EnvironmentMonitor
from .inventory_tools import route_inventory_query
from .ollama import CancellationToken, OllamaCancelled, OllamaClient, OllamaError
from .schemas import (
    ChatRequest,
    ChatResponse,
    ChatStatus,
    Ingredient,
    IngredientInput,
    IngredientSummary,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"
DEFAULT_DB_PATH = ROOT_DIR / "data" / "freshmind.db"
VALID_STATUSES = {"fresh", "soon", "expired", "low"}


def create_app(
    database_path: Path | str | None = None,
    ollama_client: OllamaClient | None = None,
    environment_monitor: EnvironmentMonitor | None = None,
) -> FastAPI:
    db = Database(database_path or os.getenv("FRESHMIND_DB_PATH", DEFAULT_DB_PATH))
    local_model = ollama_client or OllamaClient.from_env()
    climate_monitor = environment_monitor or EnvironmentMonitor.from_env()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.initialize()
        climate_monitor.start()
        try:
            yield
        finally:
            climate_monitor.stop()

    application = FastAPI(
        title="FreshMind Inventory API",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.db = db
    application.state.ollama = local_model
    application.state.environment = climate_monitor
    active_chats: dict[str, CancellationToken] = {}
    active_chats_lock = threading.Lock()

    @application.get("/api/ingredients", response_model=list[Ingredient])
    def list_ingredients(
        item_status: str | None = Query(default=None, alias="status"),
        category: str | None = None,
    ):
        if item_status and item_status not in VALID_STATUSES:
            raise HTTPException(status_code=422, detail="无效的食材状态")
        items = db.list_ingredients(category=category)
        if item_status:
            items = [item for item in items if item["status"] == item_status]
        return items

    @application.get("/api/ingredients/summary", response_model=IngredientSummary)
    def get_summary():
        items = db.list_ingredients()
        counts = {key: 0 for key in VALID_STATUSES}
        for item in items:
            counts[item["status"]] += 1
        return {"total": len(items), **counts}

    @application.post(
        "/api/ingredients", response_model=Ingredient, status_code=status.HTTP_201_CREATED
    )
    def create_ingredient(payload: IngredientInput):
        return db.create_ingredient(payload.model_dump(mode="json"))

    @application.put("/api/ingredients/{ingredient_id}", response_model=Ingredient)
    def update_ingredient(ingredient_id: int, payload: IngredientInput):
        item = db.update_ingredient(ingredient_id, payload.model_dump(mode="json"))
        if not item:
            raise HTTPException(status_code=404, detail="食材不存在")
        return item

    @application.delete(
        "/api/ingredients/{ingredient_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_ingredient(ingredient_id: int):
        if not db.delete_ingredient(ingredient_id):
            raise HTTPException(status_code=404, detail="食材不存在")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.get("/api/chat/status", response_model=ChatStatus)
    def chat_status():
        try:
            return local_model.status()
        except OllamaError:
            return {"online": False, "model": local_model.model, "installed": False}

    @application.post("/api/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest):
        inventory = db.list_ingredients()
        inventory_route = route_inventory_query(
            payload.message,
            [message.model_dump() for message in payload.history[-10:]],
            inventory,
        )
        if inventory_route:
            return {
                "answer": inventory_route.answer,
                "source": "inventory",
                "model": None,
            }
        messages = [
            {"role": "system", "content": build_system_prompt(inventory)},
            *[message.model_dump() for message in payload.history[-10:]],
            {"role": "user", "content": f"/no_think\n{payload.message}"},
        ]
        request_id = payload.request_id or str(uuid.uuid4())
        cancellation = CancellationToken()
        with active_chats_lock:
            if request_id in active_chats:
                raise HTTPException(status_code=409, detail="该回答请求正在处理中")
            active_chats[request_id] = cancellation
        try:
            answer = local_model.chat(messages, cancellation=cancellation)
        except OllamaCancelled as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except OllamaError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        finally:
            with active_chats_lock:
                active_chats.pop(request_id, None)
        return {"answer": answer, "source": "model", "model": local_model.model}

    @application.post("/api/chat/{request_id}/cancel")
    def cancel_chat(request_id: str):
        with active_chats_lock:
            cancellation = active_chats.get(request_id)
        if cancellation:
            cancellation.cancel()
            return {"cancelled": True}
        return {"cancelled": False}

    @application.get("/api/environment")
    def get_environment():
        return climate_monitor.snapshot()

    application.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @application.get("/", include_in_schema=False)
    def frontend():
        return FileResponse(FRONTEND_DIR / "index.html")

    return application


def build_system_prompt(inventory: list[dict]) -> str:
    status_labels = {
        "fresh": "状态良好",
        "soon": "临期",
        "expired": "已过期",
        "low": "余量不足",
    }
    lines = []
    for item in inventory[:100]:
        expiry = item["expiration_date"] or "未设置"
        notes = f"，备注：{item['notes'][:80]}" if item["notes"] else ""
        lines.append(
            f"- {item['name']}｜{item['category']}｜{item['quantity']:g}{item['unit']}｜"
            f"保质期：{expiry}｜{status_labels.get(item['status'], item['status'])}{notes}"
        )
    inventory_text = "\n".join(lines) if lines else "（当前没有食材）"
    return f"""你是 FreshMind 智能冰箱的本地助手。
请始终使用简洁、自然的中文回答，不展示思考过程。
库存事实查询会由后端工具处理。你只需处理菜谱建议和一般问答；涉及食材时只能依据下面提供的实时库存，不要虚构食材。
可以给出生活化建议，但涉及食品安全时要明确提醒用户自行检查实际状态。
当前温度为 4.2°C，湿度为 62%，冰箱门已关闭；这些环境数据目前是演示数据。
你只能提供信息，不能声称已经新增、修改或删除库存。

实时库存：
{inventory_text}"""


app = create_app()
