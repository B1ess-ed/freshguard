from datetime import date

from fastapi.testclient import TestClient

from backend.database import Database, ingredient_status
from backend.main import create_app
from backend.ollama import OllamaError, final_answer


class FakeOllama:
    model = "qwen3:0.6b"

    def __init__(self):
        self.messages = []

    def status(self):
        return {"online": True, "model": self.model, "installed": True}

    def chat(self, messages):
        self.messages = messages
        return "冰箱里有鲜牛奶，当前余量较低。"


class OfflineOllama(FakeOllama):
    def status(self):
        raise OllamaError("无法连接本地模型")

    def chat(self, messages):
        raise OllamaError("无法连接本地模型，请确认 Ollama 已启动")


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


def test_chat_includes_inventory_and_history(tmp_path):
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
                "message": "还有牛奶吗？",
                "history": [
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "你好，请问需要查询什么？"},
                ],
            },
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "冰箱里有鲜牛奶，当前余量较低。"
    assert "鲜牛奶" in ollama.messages[0]["content"]
    assert ollama.messages[-1] == {
        "role": "user",
        "content": "/no_think\n还有牛奶吗？",
    }


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
