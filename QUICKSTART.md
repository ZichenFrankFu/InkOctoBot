# 快捷CLI启动指南
## 1. 切换环境
```bash
conda activate InkOctoBot
```
## 2. 安装依赖
```bash
pip install -r requirements.txt
```

## 3. Spider
### 3.1. 一键启动，爬取起点 + 番茄，全部榜单
```bash
python main.py once
```
### 3.2. 只抓取某个平台 + 某个榜单
```bash
python main.py once --platform qidian --rank_key 月票榜 --qidian_pages 5 --chapter_count 5
python main.py once --platform fanqie --rank_key 新书榜科幻末世 --newbook_chapter_count 2
python main.py once --platform fanqie --rank_key 阅读榜玄幻脑洞 --chapter_count 5
```
### 3.3. 只抓某个平台（跑该平台所有榜单）
```bash
python main.py once --platform qidian --qidian_pages 2
python main.py once --platform fanqie --chapter_count 5 --newbook_chapter_count 2
```

## 4. Spider Test
### 4.1. qidian_test
#### 4.1.1 快速测试（抓取一个榜单的第一本小说，获取该小说的 metadata + 第一章，不写库，用于快速验证 HTML 结构是否发生变化）
```bash 
python tests/qidian_test.py --test quick --rank_key "月票榜"
```
#### 4.1.2 完整测试（抓取一个榜单的前三本小说及其 metadata，抓取每本小说前5章正文，写入测试数据库）
```bash 
python tests/qidian_test.py --test full --rank_key "月票榜"
```
#### 4.1.3 测试多个榜单（按多个榜单循环抓取，默认每榜单抓1本小说，并保存每本小说前3章正文）
```bash
python tests/qidian_test.py --test multi_ranks --rank_keys "月票榜,畅销榜,推荐榜"
```
#### 4.1.4 智能补全测试（只测试抓取，不写入数据库）
```bash
python tests/qidian_test.py --test smart_fetch --rank_key "月票榜" --pages 1 --chapter_n1 3 --chapter_n2 5
```

### 4.2. fanqie_test
#### 4.2.1 测试反爬字体解密（仅番茄）
```bash
python tests/fanqie_test.py --test decryption
```
#### 4.2.2 快速测试（抓取一个榜单的第一本小说，获取该小说的 metadata + 第一章，不写库，用于快速验证 HTML 结构是否发生变化）
```bash
python tests/fanqie_test.py --test quick --rank_key "阅读榜科幻末世"
```
#### 4.2.3 完整测试（顺序执行所有测试类型, 覆盖：榜单、详情、章节、智能补全、去重、字体解密、多榜单, 写入测试数据库）
```bash
python tests/fanqie_test.py --test full --rank_key "阅读榜科幻末世"
```
#### 4.2.4 测试多个榜单
```bash
python tests/fanqie_test.py --test multi_ranks --rank_keys "阅读榜西方奇幻,阅读榜科幻末世,新书榜西方奇幻"
```
#### 4.2.5 智能补全测试（只测试抓取，不写入数据库）
```bash
python tests/fanqie_test.py --test smart_fetch --rank_key "阅读榜西方奇幻" --pages 1 --chapter_n1 3 --chapter_n2 5
```
#### 4.2.6 小说改名测试
```bash
python tests/fanqie_test.py --test fake_rename
```

## 5. Analysis
```bash
python analysis/run_analysis.py --db ../outputs/data/novels.db --platform both --top_k 10 --lookback all
```

---

## 6. AI Agent & Memory System Tests

### 6.1 运行全部单元测试（无需GPU/模型）
```bash
pip install pytest
python -m pytest tests/ -v --ignore=tests/test_agents_integration.py
```

### 6.2 各模块单独测试
```bash
# 四层记忆系统 (Immediate/ChapterBuffer/EpisodicTimeline)
python -m pytest tests/test_memory_system.py -v

# 评估系统 (Repetition/Slop/StyleDrift/QualityScorer)
python -m pytest tests/test_evaluation.py -v

# 事件系统 (EventBus/Triggers)
python -m pytest tests/test_event_system.py -v

# 决策引擎 (Layer B: 效用函数/前景理论/贝叶斯信任)
python -m pytest tests/test_decision_engine.py -v

# 角色卡 + 世界书
python -m pytest tests/test_character_worldbook.py -v

# 预处理 Pipeline (章节分割/角色提取/风格指纹/节奏分析)
python -m pytest tests/test_preprocessing.py -v

# 约束系统 (优先级组装/Good-Bad示例)
python -m pytest tests/test_constraints.py -v

# 模型提供商 + 路由
python -m pytest tests/test_model_providers.py -v

# 数据库Schema + 安全模块
python -m pytest tests/test_database_schema.py -v
```

### 6.3 Agent 集成测试（需要 Ollama + DeepSeek 模型）

#### 6.3.1 模型安装
```bash
# 1. 安装 Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# 2. 导入本地 DeepSeek 模型
cd models/DeepSeek_R1_Qwen_32B
# 确保 .gguf 文件已放入此目录
ollama create deepseek-r1-qwen-32b -f Modelfile

# 3. 验证模型可用
ollama list
ollama run deepseek-r1-qwen-32b "你好"
```

#### 6.3.2 运行集成测试
```bash
# 运行全部 Agent 集成测试
python -m pytest tests/test_agents_integration.py -v -s

# 仅测试单个 Agent
python -m pytest tests/test_agents_integration.py::TestSceneDirectorIntegration -v -s
python -m pytest tests/test_agents_integration.py::TestActorAgentIntegration -v -s
python -m pytest tests/test_agents_integration.py::TestEditorWriterIntegration -v -s
python -m pytest tests/test_agents_integration.py::TestEvaluatorIntegration -v -s
python -m pytest tests/test_agents_integration.py::TestNarratorAgentIntegration -v -s
python -m pytest tests/test_agents_integration.py::TestStoryArchitectIntegration -v -s

# 端到端 Pipeline 测试 (SceneDirector → Actor → Narrator → Editor)
python -m pytest tests/test_agents_integration.py::TestFullPipelineIntegration -v -s
```

#### 6.3.3 使用自定义模型
```bash
# 通过环境变量指定不同模型名
INKOCTOBOT_TEST_MODEL="qwen2.5:14b" python -m pytest tests/test_agents_integration.py -v -s

# 指定自定义 Ollama 地址
INKOCTOBOT_OLLAMA_URL="http://192.168.1.100:11434" python -m pytest tests/test_agents_integration.py -v -s
```

### 6.4 测试覆盖范围

| 测试文件 | 模块 | 测试数 | 需要模型 |
|---------|------|-------|---------|
| `test_memory_system.py` | 四层记忆系统 | 18 | 否 |
| `test_evaluation.py` | 评估系统 | 16 | 否 |
| `test_event_system.py` | 事件系统 | 10 | 否 |
| `test_decision_engine.py` | 量化决策引擎 | 9 | 否 |
| `test_character_worldbook.py` | 角色卡+世界书 | 16 | 否 |
| `test_preprocessing.py` | 预处理Pipeline | 10 | 否 |
| `test_constraints.py` | 约束系统 | 6 | 否 |
| `test_model_providers.py` | 模型路由 | 7 | 否 |
| `test_database_schema.py` | 数据库+安全 | 9 | 否 |
| `test_agents_integration.py` | Agent端到端 | 8 | **是** |
| **合计** | | **109** | |