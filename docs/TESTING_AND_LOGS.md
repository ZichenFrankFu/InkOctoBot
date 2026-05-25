# 测试与日志查看指南

> 如何对 InkOctoBot 做测试 + 如何在出问题时找到日志、追踪问题。
> 所有命令都假设你在仓库根目录 `InkOctoBot/` 下执行。

---

## 第一部分：测试

### 1.1 测试结构（v3 per-module 布局）

测试目录**镜像源码结构**——每个顶层 Python 包对应 `tests/` 下一个
同名目录：

```
tests/
  conftest.py               # 全局 fixtures (mock_router / sample data / tmp_db)
  pytest.ini                # 标记 + asyncio 模式
  README.md                 # 测试约定 + 运行方法

  agents/                   ← 镜像 agents/
    guardrails/test_assembler.py
    evaluation/test_detectors.py
    evaluation/test_consistency_check.py
    evaluation/test_repetition_detect.py

  framework/                ← 镜像 framework/
    test_observability.py             # 单元
    test_observability_integration.py # 端到端 trace_id 流转
    test_config.py / test_event_bus.py / test_event_system.py
    test_skill_registry.py / test_skill_learner.py

  knowledge/                ← 镜像 knowledge/
    memory/test_memory_system.py
    truth/                            # 6 个 truth 测试 + integration
    test_character_worldbook.py
    test_decision_engine.py

  llm/test_base.py
  market_analysis/test_formula_engine.py
  reference_ingest/test_lora_pipeline.py
  reference_pipeline/test_advanced.py
  storage/test_project_schema.py / test_connection.py

  integration/test_agents_pipeline.py     # 跨模块端到端
```

### 1.2 安装测试依赖

```bash
pip install -r requirements.txt
# 测试还需要这几个（如未在 requirements 内）：
pip install pytest pytest-asyncio python-multipart httpx
```

### 1.3 运行测试 — 完整 cookbook

```bash
# 全部跑完
pytest tests/

# 安静模式 + 短回溯
pytest tests/ -q --tb=short

# 跑一个模块
pytest tests/knowledge/truth/
pytest tests/framework/
pytest tests/agents/evaluation/

# 跑一个文件
pytest tests/framework/test_observability.py

# 跑一个用例
pytest tests/framework/test_observability.py::test_trace_scope_binds_and_restores

# 关键词过滤
pytest tests/ -k "trace_id"
pytest tests/ -k "not integration"

# 按标记过滤（标记在 pytest.ini 定义）
pytest tests/ -m unit              # 仅单元测试
pytest tests/ -m "not integration" # 跳过需 Ollama 的集成测试

# 失败时停止
pytest tests/ -x

# 显示日志输出
pytest tests/ -s --log-cli-level=INFO

# 覆盖率
pip install pytest-cov
pytest tests/llm/ --cov=llm --cov-report=term-missing
pytest tests/ --cov=. --cov-report=html  # 输出到 htmlcov/index.html
```

### 1.4 当前测试基线

```bash
$ pytest tests/ -q --tb=line
318 passed, 9 skipped, 2 warnings in 19.36s
```

9 skipped 是依赖 Ollama 在线的集成测试——本地装好 Ollama 后会自动跑。

### 1.5 给新模块写测试

约定（详见 `tests/README.md`）：
- 测试文件镜像源码路径与命名：`agents/new_thing.py` → `tests/agents/test_new_thing.py`
- **公共 API only**：测试通过包的 `__init__.py` 导入，不要 `from foo._private import ...`
- 不在 `tests/` 子目录下放 `__init__.py`——pytest 用 rootdir 收集模式，
  否则会与同名的源码顶级包（`framework/` 等）冲突
- 用 `tests/conftest.py` 里已有的 fixtures（`mock_model_router`、
  `sample_chapter_text`、`tmp_db` 等），别重复实现

最小示例：
```python
# tests/agents/test_my_new_agent.py
from agents.my_new_agent import MyAgent

def test_my_agent_does_thing(mock_model_router):
    agent = MyAgent(mock_model_router)
    result = agent.do_thing("input")
    assert "expected" in result
```

异步测试自动支持（`asyncio_mode = auto` 已在 pytest.ini）：
```python
async def test_async_thing():
    result = await some_async_fn()
    assert result == 42
```

### 1.6 在不启 Ollama 的情况下跑集成测试

测试模式 + MockProvider：
```bash
WN_TEST_MODE=1 pytest tests/integration/
```

测试模式下 `_SimpleRouter._resolve` 会直接返回 `("mock", "mock-test-v1", {})`，
所有 LLM 调用走 `MockProvider`（返回预定义的 mock 响应）。

---

## 第二部分：日志查看

### 2.1 日志写在哪？

**跨平台**：
- **Linux**：`~/.local/state/InkOctoBot/launcher.log` （或 `$XDG_STATE_HOME`）
- **macOS**：`~/Library/Logs/InkOctoBot/launcher.log`
- **Windows**：`%LOCALAPPDATA%\InkOctoBot\launcher.log`

**应用运行日志**（FastAPI / agents / pipeline 全部）：
- `outputs/logs/inkoctobot_<时间戳>.log` （仓库目录下）

每次启动会创建新文件，文件名含时间戳。

### 2.2 日志格式

默认人类可读：
```
2026-05-25 14:23:15 | inkoctobot.agents.evaluation.evaluator | INFO    | trace=a3f9c2d8e1b4 | evaluation chapter=1 passed=True score=85 issues=0
```

字段：时间 | logger 名 | 级别 | trace_id | 消息

### 2.3 JSON 模式（机器可读）

```bash
INKOCTO_LOG_JSON=1 python launcher.py
```

每行变成一个 JSON 对象，方便用 jq / Loki / 任何日志聚合工具：
```json
{"ts": 1716640995.123, "level": "INFO", "logger": "inkoctobot.agents.evaluation.evaluator", "msg": "evaluation chapter=1 passed=True score=85 issues=0", "trace_id": "a3f9c2d8e1b4", "session_id": "gen_xyz123"}
```

用 jq 过滤：
```bash
tail -f outputs/logs/inkoctobot_*.log | jq 'select(.level == "ERROR")'
tail -f outputs/logs/inkoctobot_*.log | jq 'select(.trace_id == "a3f9c2d8e1b4")'
```

### 2.4 Logger 命名规范

所有 logger 走 `inkoctobot.*` 命名空间，按包路径分层：

| 前缀 | 内容 |
|---|---|
| `inkoctobot.launcher` | 桌面入口 |
| `inkoctobot.framework.*` | 基础设施 (skill_registry / observability / event_bus) |
| `inkoctobot.llm.router` | 路由每次调用都 INFO 记 provider/model |
| `inkoctobot.llm.<provider>` | 各 provider |
| `inkoctobot.agents.evaluation.evaluator` | 评估结果完整 JSON |
| `inkoctobot.knowledge.memory.consolidator` | L2→L3+L4 萃取 |
| `inkoctobot.knowledge.memory.semantic_store` | RAG 查询 (DEBUG) |
| `inkoctobot.knowledge.memory.knowledge_isolation` | 角色视角过滤 |
| `inkoctobot.knowledge.truth.store` | Truth File apply_deltas |
| `inkoctobot.knowledge.idea_db` | 灵感库 (idea.db) CRUD |
| `inkoctobot.storage.connection` | 统一 DB 网关 (open/commit/rollback/retry) |
| `inkoctobot.storage.market_db` | 市场 DB 操作（含 retry 日志） |
| `inkoctobot.ui.backend.*` | FastAPI 路由 |

### 2.5 调整日志级别

`framework/log_setup.py:setup_logging` 默认参数：
- 文件 handler：`DEBUG` 级（一切都写）
- 控制台：`INFO` 级
- `INKOCTO_DEBUG=1` 环境变量将控制台调到 `DEBUG`

按 logger 调整（在 Python 启动脚本里）：
```python
import logging
logging.getLogger("inkoctobot.llm.router").setLevel(logging.DEBUG)
logging.getLogger("inkoctobot.knowledge.memory").setLevel(logging.WARNING)
```

### 2.6 实时查看日志

```bash
# 跟踪最新日志
tail -f outputs/logs/inkoctobot_*.log

# 只看错误
tail -f outputs/logs/inkoctobot_*.log | grep -E "ERROR|WARN"

# 只看某模块
tail -f outputs/logs/inkoctobot_*.log | grep "inkoctobot.knowledge.memory"

# 只看某个 trace
tail -f outputs/logs/inkoctobot_*.log | grep "trace=a3f9c2d8e1b4"
```

---

## 第三部分：用 /api/debug/* 在线排查

### 3.1 启用 debug 端点

debug 端点默认关闭。两种方式启用：

```bash
# 方式 1：测试模式（最快）
python launcher.py --test

# 方式 2：环境变量（保留生产数据）
INKOCTO_DEBUG=1 python launcher.py
```

### 3.2 端点清单

| Endpoint | 用途 | 示例 |
|---|---|---|
| `GET /api/debug/status` | 探活 + flag (即使 debug 关闭也能调) | `curl http://127.0.0.1:8713/api/debug/status` |
| `GET /api/debug/recent-logs` | 最近 N 条日志，可按 level / logger / trace_id / session_id 过滤 | `curl '...?limit=50&level=ERROR'` |
| `GET /api/debug/trace/{trace_id}` | 一条 trace 的全部日志 | `curl http://.../api/debug/trace/a3f9c2d8e1b4` |
| `GET /api/debug/session/{sid}` | 一个生成 session 的全部日志 | `curl http://.../api/debug/session/gen_xyz` |
| `GET /api/debug/event-bus` | EventBus 最近事件历史 | `curl '...?event_type=EVALUATION_COMPLETED'` |
| `GET /api/debug/usage` | LLM token 使用快照 | `curl http://.../api/debug/usage` |
| `GET /api/debug/diagnostics` | 一次性快照：DB 大小 + ChromaDB 集合 + active sessions + log buffer 统计 | `curl http://.../api/debug/diagnostics` |

### 3.3 典型排查流程

**场景 1：用户生成完一章发现结果不对，要追溯哪步出问题**

```bash
# 1. 在 UI 里点开「评估结果」面板拿到 session_id（形如 gen_abcdef）
# 2. 拉这个 session 的全部日志
curl -s 'http://127.0.0.1:8713/api/debug/session/gen_abcdef' | jq '.records'

# 看到一行 "evaluation chapter=N passed=False score=42" → 用 trace_id 拉完整链路
curl -s 'http://127.0.0.1:8713/api/debug/trace/<trace_id>' | jq '.records'
```

**场景 2：怀疑 RAG 给了错误上下文**

```bash
# 开 DEBUG 级 + JSON 模式，重新生成一遍
INKOCTO_LOG_JSON=1 INKOCTO_DEBUG=1 python launcher.py
# 然后看 rag_query 日志：
tail -f outputs/logs/inkoctobot_*.log | jq 'select(.logger | contains("semantic_store"))'
# 会看到每次查询的 hit 数 + top-k 距离
```

**场景 3：DB 操作变慢，怀疑锁争用**

```bash
# 关闭 GAP 6 后 db_retry 会显式打到 WARN，搜一下：
grep "db_retry" outputs/logs/inkoctobot_*.log

# 或在线查：
curl -s 'http://127.0.0.1:8713/api/debug/recent-logs?level=WARNING&logger_prefix=inkoctobot.storage' | jq
```

**场景 4：评估器一直 fail，要知道哪个 dimension 分最低**

```bash
# Evaluator 的 INFO 日志只给汇总；DEBUG 给完整 JSON
INKOCTO_DEBUG=1 python launcher.py
# 然后过滤：
grep "evaluation chapter=" outputs/logs/inkoctobot_*.log | tail -5
# 或拉 EventBus：
curl -s 'http://127.0.0.1:8713/api/debug/event-bus?event_type=EVALUATION_COMPLETED' | jq '.events[-1]'
```

**场景 5：Skill 学习失败，要知道 LLM 生成了什么、为什么被拒**

```bash
# SkillLearner 把每次提议（accepted / rejected / installed）全部 log 了
grep "skill_proposal" outputs/logs/inkoctobot_*.log
# 拒绝行会带 code_head 字段，看 LLM 到底生成了啥
```

### 3.4 trace_id 端到端

每个 HTTP 请求由 `TraceIDMiddleware` 自动绑定一个 trace_id（也可以
通过 `X-Request-ID` 头自定义传入），背景任务（生成流水线）由
`trace_scope(...)` 同时绑定 session_id。两个 ID 通过 Python contextvars
**自动**传到子模块（包括 asyncio 任务和子线程的 LLM 调用）。

```bash
# 客户端指定 trace_id
curl -H 'X-Request-ID: my-trace-001' http://127.0.0.1:8713/api/projects

# 响应头 X-Request-ID 会回显
# 然后查这一条 trace 的所有日志
curl 'http://127.0.0.1:8713/api/debug/trace/my-trace-001' | jq
```

---

## 第四部分：开发者工具

### 4.1 跑某段代码的 import 是否还正常

```bash
python -c "from ui.backend.app.main import app; print('OK', len(app.routes))"
python -c "import knowledge.truth; print('OK')"
```

### 4.2 检查 fastapi 全部路由

```bash
python -c "
from ui.backend.app.main import app
for r in sorted(app.routes, key=lambda x: getattr(x, 'path', '')):
    print(getattr(r, 'path', '?'), '->', getattr(r, 'methods', ''))
" | head -50
```

### 4.3 快速观测一次生成的完整流转

```bash
# 终端 A：开 DEBUG + JSON 日志启动
INKOCTO_DEBUG=1 INKOCTO_LOG_JSON=1 python launcher.py

# 终端 B：实时跟踪 generation 相关
tail -f outputs/logs/inkoctobot_*.log | jq 'select(.logger | test("generation|pipeline|evaluator|consolidator|base_agent"))'

# 用 UI 触发一次生成，B 终端就会看到完整 trace
```

### 4.4 健康检查 + 诊断

```bash
# 一次性快照
curl -s http://127.0.0.1:8713/api/debug/diagnostics | jq

# 输出形如：
# {
#   "repo_root": "/home/user/InkOctoBot",
#   "data_dir": null,
#   "test_mode": false,
#   "databases": {
#     "novels.db": {"exists": true, "bytes": 12345678},
#     "InkOctoBot_Crawler.db": {"exists": true, "bytes": 9876543},
#     "references.db": {"exists": true, "bytes": 0}
#   },
#   "log_buffer": {
#     "total_records": 487,
#     "by_level": {"DEBUG": 120, "INFO": 320, "WARNING": 35, "ERROR": 12, "CRITICAL": 0}
#   },
#   "active_generation_sessions": 1
# }
```

### 4.5 一些常用 grep 模式

```bash
# 所有 LLM 调用
grep "invoke prep" outputs/logs/inkoctobot_*.log

# 所有失败的 LLM 调用（带 exc_info）
grep "invoke FAILED" outputs/logs/inkoctobot_*.log

# 所有 provider 错误
grep "provider FAILED" outputs/logs/inkoctobot_*.log

# 所有评估
grep "evaluation chapter=" outputs/logs/inkoctobot_*.log

# 所有记忆萃取
grep "consolidator chapter=" outputs/logs/inkoctobot_*.log

# 所有知识隔离激进警告
grep "knowledge_filter aggressive" outputs/logs/inkoctobot_*.log

# 所有 DB 重试
grep "db_retry" outputs/logs/inkoctobot_*.log

# 所有 skill 提议
grep "skill_proposal" outputs/logs/inkoctobot_*.log
```

---

## 第五部分：CI（未来）

当前**没有** CI；架构 review plan 里 Phase 7 会加最小可行的
`.github/workflows/ci.yml`：

```yaml
# 计划草案
name: CI
on: [push, pull_request]
jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -r requirements.txt pytest pytest-asyncio
      - run: pytest tests/ -x --tb=short
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: {node-version: "20"}
      - run: cd ui/frontend && npm ci && npm run build
```

---

## 参考

- `tests/README.md` — 测试目录详细约定
- `framework/observability/WORKFLOW.md` — observability 内部架构
- `agents/evaluation/WORKFLOW.md` — 评估流水线
- `knowledge/memory/WORKFLOW.md` — 4 层记忆系统
- 主 README §5.3 — Logger 命名规范概览
