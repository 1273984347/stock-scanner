# TradingAgents 模块迁移计划

## 目标

从 TradingAgents 提取最有价值的模块，迁移到 stock-scanner，提升系统能力。

## 优先级与工作量

| 阶段 | 模块 | 工作量 | 价值 | 优先级 |
|:----:|------|:------:|------|:------:|
| 1 | `structured.py` — 结构化输出降级 | 0.5 天 | 高 | P1 |
| 2 | `memory.py` — 历史学习机制 | 2-3 天 | 中高 | P2 |
| 3 | `interface.py` — Vendor Fallback | 2 天 | 中 | P3 |

---

## Phase 1: `structured.py` 迁移

### 目标
将 LLM 结构化输出降级机制迁移到 stock-scanner，提高 AI 分析可靠性。

### 新增文件
- `lib/llm_utils/structured.py` — 结构化输出降级工具

### 核心函数
```python
def bind_structured(llm, schema, agent_name) -> Optional[Any]:
    """检测 provider 是否支持 with_structured_output"""

def invoke_structured_or_freetext(structured_llm, plain_llm, prompt, render, agent_name) -> str:
    """双重降级调用包装器"""
```

### 验证方式
```bash
# 测试降级路径
python -m pytest tests/test_llm_utils.py -v
```

### 风险
- 低风险，纯工具函数，不影响现有逻辑

---

## Phase 2: `memory.py` 迁移

### 目标
将历史学习机制迁移到 stock-scanner，实现决策日志 + 反思学习。

### 新增文件
- `lib/llm_utils/memory.py` — `TradingMemoryLog` 类
- `lib/llm_utils/rating.py` — 5-tier Rating 解析器
- `tests/test_memory_log.py` — 单元测试

### 核心功能
1. **决策日志**：`store_decision(ticker, rating, trade_date)`
2. **结果更新**：`update_with_outcome(trade_date, ticker, return_pct, alpha_pct, holding_days, reflection)`
3. **上下文检索**：`get_past_context(ticker, n_same=5, n_cross=3)`

### 集成点
- `lib/llm_analysis.py` — 在生成分析前调用 `get_past_context()` 注入历史经验
- `lib/pipeline.py` — 在回测结果处理后调用 `update_with_outcome()`

### 验证方式
```bash
# 测试内存日志读写
python -m pytest tests/test_memory_log.py -v
```

### 风险
- 中等风险，涉及数据持久化和集成

---

## Phase 3: `interface.py` 迁移

**已跳过** — stock-scanner 已有 `get_stock_hist_multi_source` 多源回退，Vendor 抽象层迁移成本与收益不成比例。

---

## 执行顺序

```
Phase 1 (0.5天)
    ↓ 验证通过后
Phase 2 (2-3天)
    ↓ 验证通过后
Phase 3 (2天)
    ↓
全部验证
```

## 回滚计划

每个 Phase 完成后立即提交，失败时 `git revert` 即可回滚。

---

## 依赖关系

- Phase 1 无依赖
- Phase 2 依赖 Phase 1（结构化输出可能需要）
- Phase 3 独立，但依赖 stock-scanner 现有数据源架构稳定
