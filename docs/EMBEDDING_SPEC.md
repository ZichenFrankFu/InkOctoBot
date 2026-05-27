# InkOctoBot Embedding 系统改造规格

下面是给 Claude Code 的完整需求 spec。

## 一、改造目标

把当前散落在各 loader 的 embedding 调用，统一为一个可切换 model + 自适应硬件的 EmbeddingService 层。

支持的功能：
- 多 model 注册和切换
- 中英文模式 toggle，自动选择推荐 model
- 自适应硬件运行（GPU 优先，无 GPU 降级 CPU）
- Lazy load 模型（首次使用时下载和加载）
- 全局单例，避免重复加载
- 切换 model 时的批量 reindex 工具

## 二、Model 注册表设计

### 2.1 支持的 model 清单

定义在 `services/embedding/registry.py`：

```
中文 model（中文模式可选）:
  - BGE-base-zh-v1.5          [默认]
  - BGE-large-zh-v1.5
  - Qwen3-Embedding-8B
  - Conan-Embedding-v2

英文 model（英文模式可选）:
  - bge-m3                    [默认]
  - text2vec-base-multilingual
```

英文模式 toggle 切换时，自动从中文 model 切到英文默认（bge-m3）。

### 2.2 每个 model 需要的元信息

```
- model_key:        唯一标识（如 "bge-base-zh"）
- display_name:     UI 显示名
- hf_repo:          Hugging Face 仓库 ID
- language:         'zh' / 'en' / 'multilingual'
- dimension:        向量维度
- recommended_for:  推荐用途列表
- min_vram_mb:      GPU 显存需求
- min_ram_mb:       CPU 内存需求
- model_size_mb:    模型文件大小
- is_default_zh:    中文模式默认
- is_default_en:    英文模式默认
```

### 2.3 具体 model 元信息表

```
BGE-base-zh-v1.5:        BAAI/bge-base-zh-v1.5         dim 768  vram 1500  default zh
BGE-large-zh-v1.5:       BAAI/bge-large-zh-v1.5        dim 1024 vram 3000
Qwen3-Embedding-8B:      Qwen/Qwen3-Embedding-8B       dim 4096 vram 16000
Conan-Embedding-v2:      TencentBAC/Conan-embedding-v2 dim 1792 vram 3000
bge-m3:                  BAAI/bge-m3                   dim 1024 vram 2500  default en (multilingual)
text2vec-base-multilingual: shibing624/text2vec-base-multilingual dim 384 vram 800
```

HF 路径已 WebSearch 验证：Qwen3-Embedding-8B、TencentBAC/Conan-embedding-v2 都存在。

## 三、Settings 配置

```
embedding_language_mode:    'zh' | 'en'              默认 'zh'
embedding_model_key:        当前激活的 model key      默认 'bge-base-zh'
```

约束：
- 切 language_mode 时强制 model_key 切到对应语言默认
- 同语言可自由切 model
- 切 model 后必须触发 reindex

## 四、EmbeddingService 接口

### 4.1 模块结构

```
ui/backend/app/services/embedding/
├── __init__.py
├── registry.py              # model 注册表
├── service.py               # EmbeddingService 主类
├── hardware_detector.py     # GPU / 显存检测
├── batch_reindex.py         # 批量重算（Phase 3）
└── providers/
    ├── __init__.py
    ├── base.py             # Provider 抽象基类
    └── transformers.py     # HF transformers 实现
```

### 4.2 EmbeddingService 方法

```
get_current_model() -> ModelInfo
embed(text: str) -> bytes
embed_batch(texts: list[str]) -> list[bytes]
get_dimension() -> int
is_text_hash_match(stored_hash: str, current_text: str) -> bool
compute_text_hash(text: str) -> str
switch_model(model_key: str) -> SwitchResult
is_ready() -> bool
estimate_load_time() -> int
```

### 4.3 Provider 抽象基类

```
load() -> None
unload() -> None
embed(texts: list[str], normalize: bool = True) -> np.ndarray
get_device() -> str   # 'cuda' / 'cpu' / 'mps'
```

只实现 `TransformersProvider`（sentence-transformers）。未来可加 `OpenAIProvider`。

## 五、硬件自适应

1. 调硬件检测器 → has_cuda / cuda_vram_mb / ram_mb
2. 决定 device：has_cuda && vram >= min_vram_mb → 'cuda'，否则 'cpu'
3. 决定 dtype：GPU 用 fp16，CPU 用 fp32
4. 显存不足 OOM → 自动降级 CPU，日志明确记录，settings status 字段暴露

### SwitchResult

```
{ success, device_decision, warnings, need_reindex, affected_tables }
```

## 六、Reindex 工具

### 触发场景
- 切换 embedding_model_key
- 维度变化
- 手动触发

### 受影响表
```
worldbook_entries          (embedding_json + embedding_text_hash)
inspirations               (embedding_json + embedding_text_hash)
subplot_threads            (embedding_json + embedding_text_hash)
skill_index                (embedding + body_snippet 等)
reference_chapters         (embedding_json + embedding_text_hash)
ChromaDB collections       (project_xxx_chunks 等)
```

### 流程要求
1. 进度可见（每表 + 总体 + ETA）
2. 可中断 + 可恢复
3. 失败隔离（单条失败不影响整批）
4. ChromaDB 维度变化时 drop + recreate collection
5. 异步执行（任务 ID）

### 接口

```
start_reindex(model_key=None) -> TaskId
get_reindex_status(task_id) -> ReindexStatus
cancel_reindex(task_id) -> None
```

## 七、Loader 改造

7.1 不再直接调 SentenceTransformer。统一用 `service.embed()`。

7.2 处理维度不匹配：读 embedding 时比对 `embedding_model_key`，不匹配则跳过（不参与本次排序），并 emit warning。

7.3 加 `embedding_model_key` 列到 5 张表 + ChromaDB collection 名带 model_key 后缀。

## 八、API 端点

```
GET    /api/embedding/models
GET    /api/embedding/current
GET    /api/embedding/hardware-status
POST   /api/embedding/switch
POST   /api/embedding/reindex
GET    /api/embedding/reindex/{task_id}
POST   /api/embedding/reindex/{task_id}/cancel
POST   /api/settings/embedding-language-mode
```

## 九、依赖

```
sentence-transformers
transformers
torch
einops      # Qwen3 需要
```

## 十、默认值

```
默认 model:        bge-base-zh
默认 language:     zh
LRU cache:         4096
batch_size:        32
GPU fp16 / CPU fp32
首次启动不预加载（lazy）
切换 model 时释放旧 model + gc.collect() + torch.cuda.empty_cache()
```

## 十一、错误处理

1. HF 下载失败 → 明确错误，不静默
2. OOM → 自动降级 CPU
3. tokenizer 加载失败 → 明确报错
4. NaN / 全 0 → 视为错误，不写库

## 十二、测试要求

```
test_embedding_registry.py
test_embedding_service.py
test_hardware_adaptation.py
test_model_switching.py
test_language_mode_toggle.py
test_reindex.py
```

## 十三、实施 Phase

```
Phase 1: 基础（registry + provider + service + hardware）
Phase 2: Loader 改造
Phase 3: Reindex 工具
Phase 4: 语言切换
Phase 5: 测试 + 验收
```
