# FreshMind 智能冰箱

一个本地运行的智能冰箱管理原型，提供基于 SQLite 的库存维护，并通过 Ollama 调用开发板上的本地大语言模型。

## 环境要求

- Python 3.11+

## 启动

```powershell
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

打开 <http://127.0.0.1:8000>。接口文档位于 <http://127.0.0.1:8000/docs>。

数据库默认保存在 `data/freshmind.db`。如需指定其他路径，可参考 `.env.example` 设置 `FRESHMIND_DB_PATH` 环境变量。

## 本地模型

先确认 Ollama 和模型可用：

```bash
ollama list
curl http://127.0.0.1:11434/api/tags
```

默认调用 `qwen3:0.6b`。如开发板中的模型名称不同，请在启动服务前设置：

```bash
export OLLAMA_BASE_URL=http://127.0.0.1:11434
export OLLAMA_MODEL=qwen3:0.6b
export OLLAMA_TIMEOUT=180
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

智能体会自动把当前 SQLite 库存加入模型上下文。该接口仅提供问答，不允许模型直接修改库存。

模型状态接口为 `GET /api/chat/status`，对话接口为 `POST /api/chat`。

### 更新到开发板

在电脑的项目目录执行：

```powershell
scp -r .\backend .\frontend .\tests .\requirements.txt .\README.md .\.env.example 用户名@开发板IP:~/freshmind/
```

开发板上重新进入虚拟环境并更新依赖：

```bash
cd ~/freshmind
source .venv/bin/activate
pip install -r requirements.txt
```

## 测试

```powershell
python -m pytest
```

## 项目结构

```text
backend/       FastAPI 接口、数据校验和 SQLite 数据访问
frontend/      原生 HTML、CSS 与模块化 JavaScript
tests/         API、状态计算和持久化测试
data/          本地数据库目录（数据库文件不提交）
```
