from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    """Raised when the local Ollama service cannot complete a request."""


@dataclass
class OllamaClient:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:0.6b"
    timeout: float = 180.0

    @classmethod
    def from_env(cls) -> "OllamaClient":
        return cls(
            base_url=os.getenv("OLLAMA_BASE_URL", cls.base_url).rstrip("/"),
            model=os.getenv("OLLAMA_MODEL", cls.model),
            timeout=float(os.getenv("OLLAMA_TIMEOUT", str(cls.timeout))),
        )

    def chat(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.2, "num_predict": 320},
        }
        response = self._request("/api/chat", method="POST", payload=payload)
        answer = final_answer(response.get("message", {}).get("content", ""))
        if not answer:
            raise OllamaError("本地模型没有返回最终回答，请重试")
        return answer

    def status(self) -> dict[str, Any]:
        response = self._request("/api/tags")
        models = [item.get("name", "") for item in response.get("models", [])]
        expected_names = {self.model}
        if ":" not in self.model:
            expected_names.add(f"{self.model}:latest")
        installed = bool(expected_names.intersection(models))
        return {"online": True, "model": self.model, "installed": installed}

    def _request(
        self, path: str, method: str = "GET", payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama 请求失败（HTTP {error.code}）：{detail[:160]}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise OllamaError("无法连接本地模型，请确认 Ollama 已启动") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise OllamaError("本地模型返回了无法解析的数据") from error


def final_answer(content: str) -> str:
    """Remove reasoning blocks that some thinking models include in content."""
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.IGNORECASE | re.DOTALL)
    if re.search(r"<think>", cleaned, flags=re.IGNORECASE):
        return ""
    return cleaned.strip()
