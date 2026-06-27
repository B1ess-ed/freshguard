from datetime import date
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from backend.database import Database, ingredient_status
from backend.environment import decode_sht30
from backend.main import create_app
from backend.ollama import OllamaCancelled, OllamaError, final_answer


class FakeOllama:
    model = "qwen3:0.6b"

    def __init__(self):
        self.messages = []
        self.call_count = 0

    def status(self):
        return {"online": True, "model": self.model, "installed": True}

    def chat(self, messages, cancellation=None):
        self.call_count += 1
        self.messages = messages
        return "这是模型回答。"


class OfflineOllama(FakeOllama):
    def status(self):
        raise OllamaError("无法连接本地模型")

    def chat(self, messages, cancellation=None):
        raise OllamaError("无法连接本地模型，请确认 Ollama 已启动")


class BlockingOllama(FakeOllama):
    def __init__(self):
        super().__init__()
        self.started = Event()

    def chat(self, messages, cancellation=None):
        self.call_count += 1
        self.started.set()
        assert cancellation is not None
        cancellation.wait(timeout=2)
        if cancellation.cancelled:
            raise OllamaCancelled("回答已停止")
        return "等待超时"


class FakeEnvironmentMonitor:
    def __init__(self):
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def snapshot(self):
        sample = {
            "timestamp": "2026-06-27T00:00:00+00:00",
            "temperature_c": 4.25,
            "humidity": 61.5,
        }
        return {
            "online": True,
            "sensor": {
                "model": "SHT30",
                "bus": 4,
                "address": "0x44",
                "interval_seconds": 10,
            },
            "latest": sample,
            "history": [sample],
            "last_error": None,
        }


def sample_payload(**overrides):
    payload = {
        "name": "测试番茄",
        "category": "蔬菜",
        "quantity": 3,
        "unit": "个",
        "expiration_date": "2026-07-01",
        "notes": "用于接口测试",
        "low_stock_threshold": 1,
    }
    payload.update(overrides)
    return payload


def test_database_initialization_is_idempotent(tmp_path):
    database = Database(tmp_path / "inventory.db")
    database.initialize()
    first_count = len(database.list_ingredients())
    database.initialize()
    assert first_count == 12
    assert len(database.list_ingredients()) == 12


def test_status_calculation():
    today = date(2026, 6, 21)
    assert ingredient_status(1, 1, None, today) == "low"
    assert ingredient_status(2, None, "2026-06-20", today) == "expired"
    assert ingredient_status(2, None, "2026-06-24", today) == "soon"
    assert ingredient_status(2, None, "2026-06-25", today) == "fresh"


def test_sht30_decode_checks_crc_and_values():
    temperature, humidity = decode_sht30(bytes.fromhex("6666938000A2"))

    assert round(temperature, 1) == 25.0
    assert round(humidity, 1) == 50.0


def test_environment_endpoint_returns_monitor_snapshot(tmp_path):
    monitor = FakeEnvironmentMonitor()
    app = create_app(tmp_path / "environment.db", environment_monitor=monitor)
    with TestClient(app) as client:
        response = client.get("/api/environment")

    assert monitor.started is True
    assert monitor.stopped is True
    assert response.status_code == 200
    assert response.json()["sensor"]["model"] == "SHT30"
    assert response.json()["sensor"]["bus"] == 4
    assert response.json()["latest"]["temperature_c"] == 4.25


def test_crud_and_summary(tmp_path):
    app = create_app(tmp_path / "api.db")
    with TestClient(app) as client:
        initial = client.get("/api/ingredients/summary")
        assert initial.status_code == 200
        assert initial.json()["total"] == 12

        created = client.post("/api/ingredients", json=sample_payload())
        assert created.status_code == 201
        ingredient_id = created.json()["id"]
        assert created.json()["name"] == "测试番茄"

        updated = client.put(
            f"/api/ingredients/{ingredient_id}",
            json=sample_payload(name="测试番茄（已修改）", quantity=0.5),
        )
        assert updated.status_code == 200
        assert updated.json()["name"] == "测试番茄（已修改）"
        assert updated.json()["status"] == "low"

        low_items = client.get("/api/ingredients", params={"status": "low"})
        assert low_items.status_code == 200
        assert ingredient_id in [item["id"] for item in low_items.json()]

        category_items = client.get("/api/ingredients", params={"category": "蔬菜"})
        assert all(item["category"] == "蔬菜" for item in category_items.json())

        deleted = client.delete(f"/api/ingredients/{ingredient_id}")
        assert deleted.status_code == 204
        assert client.delete(f"/api/ingredients/{ingredient_id}").status_code == 404


def test_validation_and_missing_records(tmp_path):
    app = create_app(tmp_path / "validation.db")
    with TestClient(app) as client:
        invalid = client.post("/api/ingredients", json=sample_payload(quantity=-1))
        assert invalid.status_code == 422
        assert client.get("/api/ingredients", params={"status": "unknown"}).status_code == 422
        assert client.put("/api/ingredients/99999", json=sample_payload()).status_code == 404


def test_data_persists_across_app_restarts(tmp_path):
    database_path = tmp_path / "persistent.db"
    with TestClient(create_app(database_path)) as client:
        created = client.post("/api/ingredients", json=sample_payload(name="持久化食材"))
        ingredient_id = created.json()["id"]

    with TestClient(create_app(database_path)) as client:
        items = client.get("/api/ingredients").json()
        assert any(item["id"] == ingredient_id and item["name"] == "持久化食材" for item in items)


def test_non_inventory_chat_uses_model_and_history(tmp_path):
    ollama = FakeOllama()
    app = create_app(tmp_path / "chat.db", ollama_client=ollama)
    with TestClient(app) as client:
        model_status = client.get("/api/chat/status")
        assert model_status.json() == {
            "online": True,
            "model": "qwen3:0.6b",
            "installed": True,
        }

        response = client.post(
            "/api/chat",
            json={
                "message": "你好",
                "history": [
                    {"role": "user", "content": "早上好"},
                    {"role": "assistant", "content": "早上好！"},
                ],
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "这是模型回答。",
        "source": "model",
        "model": "qwen3:0.6b",
    }
    assert "鲜牛奶" in ollama.messages[0]["content"]
    assert ollama.messages[-1] == {
        "role": "user",
        "content": "/no_think\n你好",
    }


def test_inventory_queries_bypass_model(tmp_path):
    ollama = FakeOllama()
    app = create_app(tmp_path / "inventory-chat.db", ollama_client=ollama)
    with TestClient(app) as client:
        existence = client.post("/api/chat", json={"message": "还有牛奶吗？"})
        quantity = client.post("/api/chat", json={"message": "鲜牛奶还剩多少？"})
        summary = client.post("/api/chat", json={"message": "库存一共有多少种食材？"})
        category = client.post("/api/chat", json={"message": "奶制品有哪些？"})

    assert existence.json()["source"] == "inventory"
    assert existence.json()["model"] is None
    assert "鲜牛奶" in existence.json()["answer"]
    assert "0.22盒" in quantity.json()["answer"]
    assert "12 种" in summary.json()["answer"]
    assert "鲜牛奶" in category.json()["answer"]
    assert ollama.call_count == 0


def test_inventory_status_expiry_and_missing_queries(tmp_path):
    ollama = FakeOllama()
    app = create_app(tmp_path / "status-chat.db", ollama_client=ollama)
    with TestClient(app) as client:
        soon = client.post("/api/chat", json={"message": "有哪些食材快过期？"}).json()
        low = client.post("/api/chat", json={"message": "低库存食材有哪些？"}).json()
        expiry = client.post("/api/chat", json={"message": "鲜牛奶什么时候过期？"}).json()
        missing = client.post("/api/chat", json={"message": "有榴莲吗？"}).json()
        missing_expiry = client.post("/api/chat", json={"message": "榴莲什么时候过期？"}).json()

    assert "临期" in soon["answer"]
    assert "余量不足" in low["answer"]
    assert "鲜牛奶的保质期是" in expiry["answer"]
    assert missing["answer"] == "库存中没有找到“榴莲”。"
    assert missing_expiry["answer"] == "库存中没有找到“榴莲”。"
    assert ollama.call_count == 0


def test_inventory_query_uses_recent_entity_context(tmp_path):
    ollama = FakeOllama()
    app = create_app(tmp_path / "context-chat.db", ollama_client=ollama)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={
                "message": "它什么时候过期？",
                "history": [
                    {"role": "user", "content": "鲜牛奶还有吗？"},
                    {"role": "assistant", "content": "有。"},
                ],
            },
        )

    assert response.json()["source"] == "inventory"
    assert "鲜牛奶的保质期是" in response.json()["answer"]
    assert ollama.call_count == 0


def test_inventory_partial_name_ambiguity_requests_clarification(tmp_path):
    ollama = FakeOllama()
    app = create_app(tmp_path / "ambiguous-chat.db", ollama_client=ollama)
    with TestClient(app) as client:
        client.post("/api/ingredients", json=sample_payload(name="纯牛奶", category="奶制品", unit="盒"))
        response = client.post("/api/chat", json={"message": "牛奶还有吗？"})

    assert response.json()["source"] == "inventory"
    assert "鲜牛奶" in response.json()["answer"]
    assert "纯牛奶" in response.json()["answer"]
    assert "完整名称" in response.json()["answer"]
    assert ollama.call_count == 0


def test_recipe_and_environment_questions_still_use_model(tmp_path):
    ollama = FakeOllama()
    app = create_app(tmp_path / "model-routing.db", ollama_client=ollama)
    with TestClient(app) as client:
        recipe = client.post("/api/chat", json={"message": "用现有食材推荐一道菜"})
        environment = client.post("/api/chat", json={"message": "现在温度是多少？"})

    assert recipe.json()["source"] == "model"
    assert environment.json()["source"] == "model"
    assert ollama.call_count == 2


def test_inventory_query_works_when_ollama_is_offline(tmp_path):
    app = create_app(tmp_path / "offline-inventory.db", ollama_client=OfflineOllama())
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "还有牛奶吗？"})

    assert response.status_code == 200
    assert response.json()["source"] == "inventory"


def test_in_progress_model_answer_can_be_cancelled(tmp_path):
    ollama = BlockingOllama()
    app = create_app(tmp_path / "cancel-chat.db", ollama_client=ollama)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            client.post,
            "/api/chat",
            json={"message": "推荐一道菜", "request_id": "cancel-test-123"},
        )
        assert ollama.started.wait(timeout=1)
        cancelled = client.post("/api/chat/cancel-test-123/cancel")
        response = pending.result(timeout=2)

    assert cancelled.json() == {"cancelled": True}
    assert response.status_code == 409
    assert response.json()["detail"] == "回答已停止"


def test_inventory_empty_zero_and_missing_expiry_answers(tmp_path):
    ollama = FakeOllama()
    app = create_app(tmp_path / "edge-chat.db", ollama_client=ollama)
    with TestClient(app) as client:
        zero = client.post(
            "/api/ingredients",
            json=sample_payload(name="零库存测试品", quantity=0, unit="个"),
        ).json()
        no_expiry = client.post(
            "/api/ingredients",
            json=sample_payload(name="无日期测试品", expiration_date=None),
        ).json()
        zero_answer = client.post("/api/chat", json={"message": "还有零库存测试品吗？"}).json()
        expiry_answer = client.post("/api/chat", json={"message": "无日期测试品保质期？"}).json()
        clarification = client.post("/api/chat", json={"message": "帮我查询库存"}).json()

        for item in client.get("/api/ingredients").json():
            client.delete(f"/api/ingredients/{item['id']}")
        empty_answer = client.post("/api/chat", json={"message": "库存里有什么？"}).json()

    assert zero["quantity"] == 0
    assert no_expiry["expiration_date"] is None
    assert "当前数量为 0个" in zero_answer["answer"]
    assert expiry_answer["answer"] == "无日期测试品未设置保质期。"
    assert clarification["answer"].startswith("请告诉我具体食材名称")
    assert empty_answer["answer"] == "当前库存为空。"
    assert ollama.call_count == 0


def test_inventory_router_is_not_limited_to_first_100_items(tmp_path):
    ollama = FakeOllama()
    app = create_app(tmp_path / "large-chat.db", ollama_client=ollama)
    with TestClient(app) as client:
        for index in range(105):
            client.post(
                "/api/ingredients",
                json=sample_payload(name=f"批量食材{index}", category="批量"),
            )
        response = client.post("/api/chat", json={"message": "批量食材104还有吗？"})

    assert response.json()["source"] == "inventory"
    assert "批量食材104" in response.json()["answer"]
    assert ollama.call_count == 0


def test_chat_reports_ollama_unavailable(tmp_path):
    app = create_app(tmp_path / "offline.db", ollama_client=OfflineOllama())
    with TestClient(app) as client:
        model_status = client.get("/api/chat/status").json()
        assert model_status["online"] is False
        response = client.post("/api/chat", json={"message": "你好"})

    assert response.status_code == 503
    assert "Ollama" in response.json()["detail"]


def test_thinking_content_is_hidden():
    content = "<think>我需要先检查库存。\n牛奶还有多少？</think>\n冰箱里有一盒鲜牛奶。"
    assert final_answer(content) == "冰箱里有一盒鲜牛奶。"
    assert final_answer("<think>尚未完成思考") == ""
