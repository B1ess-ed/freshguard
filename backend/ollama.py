from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(RuntimeError):
    """Raised when the local Ollama service cannot complete a request."""


class OllamaCancelled(OllamaError):
    """Raised when the user stops an in-progress model response."""


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._close_response = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._cancelled.wait(timeout)

    def attach(self, response) -> None:
        with self._lock:
            if self.cancelled:
                try:
                    response.close()
                except (OSError, ValueError):
                    pass
            else:
                self._close_response = response.close

    def detach(self) -> None:
        with self._lock:
            self._close_response = None

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            close_response = self._close_response
        if close_response:
            try:
                close_response()
            except (OSError, ValueError):
                pass


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

    def chat(
        self,
        messages: list[dict[str, str]],
        cancellation: CancellationToken | None = None,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {"temperature": 0.2, "num_predict": 320},
        }
        answer = final_answer(self._stream_chat(payload, cancellation or CancellationToken()))
        if not answer:
            raise OllamaError("本地模型没有返回最终回答，请重试")
        return answer

    def _stream_chat(self, payload: dict[str, Any], cancellation: CancellationToken) -> str:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/chat",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        response = None
        chunks: list[str] = []
        try:
            response = urlopen(request, timeout=self.timeout)
            cancellation.attach(response)
            if cancellation.cancelled:
                raise OllamaCancelled("回答已停止")
            while True:
                if cancellation.cancelled:
                    raise OllamaCancelled("回答已停止")
                line = response.readline()
                if not line:
                    break
                event = json.loads(line.decode("utf-8"))
                chunks.append(event.get("message", {}).get("content", ""))
                if event.get("done"):
                    break
            if cancellation.cancelled:
                raise OllamaCancelled("回答已停止")
            return "".join(chunks)
        except OllamaCancelled:
            raise
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise OllamaError(f"Ollama 请求失败（HTTP {error.code}）：{detail[:160]}") from error
        except (URLError, TimeoutError, OSError, ValueError) as error:
            if cancellation.cancelled:
                raise OllamaCancelled("回答已停止") from error
            raise OllamaError("无法连接本地模型，请确认 Ollama 已启动") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            if cancellation.cancelled:
                raise OllamaCancelled("回答已停止") from error
            raise OllamaError("本地模型返回了无法解析的数据") from error
        finally:
            cancellation.detach()
            if response is not None:
                response.close()

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
