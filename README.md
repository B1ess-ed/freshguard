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

聊天后端会先进行工具路由。有无、余量、总数、分类、低库存、临期、过期和保质期等库存事实查询会直接读取 SQLite，并由后端模板生成回答，不经过模型；因此 Ollama 离线时库存查询仍然可用。菜谱建议和一般问答继续交给本地模型，并会把当前库存作为上下文。该接口仅提供只读问答，不允许模型直接修改库存。

模型状态接口为 `GET /api/chat/status`，对话接口为 `POST /api/chat`。
`POST /api/chat` 的响应包含 `source` 字段，值为 `inventory` 或 `model`。库存直答的 `model` 为 `null`，模型回答则返回实际模型名称。食材名称采用精确匹配优先、唯一包含匹配其次；存在多个候选或缺少查询条件时，后端会要求用户澄清，而不会让模型猜测库存。

前端在模型生成期间会显示“停止”按钮。聊天请求通过可选的 `request_id` 标识，调用 `POST /api/chat/{request_id}/cancel` 可以中止对应的 Ollama 流；已停止的聊天请求返回 HTTP 409 和“回答已停止”。

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
