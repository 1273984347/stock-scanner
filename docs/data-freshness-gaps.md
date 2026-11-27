# 数据新鲜度与缺口清单

> 盘点时间：2026-11-26 19:10。公开仓不含数据文件；以下均为本地数据。
> 数据源层级：`data/store/` = 项目内持续更新仓（manifest 记账）；`data/exports/bulk/` = 一次性全市场导出快照。

## 2026-11-27 复核（blocks 已补齐）

- `blocks` 全量重跑 **5,635/5,635、fail=0**，逐文件复核 stale=0（全仓 mtime 11-27）；
  与重跑前快照比对 5,290 一致 / 345 变化 / 0 新增消失，变化为清掉伪二级标签（质量改善）。
- 11-26 的 750 只差额确认为**瞬时板块失败**，非退市（742 只当日仍在交易、8 只 B 股）；
  详见 `scripts/audit_blocks_gap.py` 与坑表 P-146。
- 剩余缺口：**23 只新股**（如 688795 摩尔线程、688802 沐曦股份）快照有但新浪行业从未收录。
- 已加覆盖率判据：`sync_blocks` 写仓前按 `max(上一版文件数, 快照标的数)` 校验，<98% 整批不写并退出非 0。
- manifest 现在直接显示批次结果：`fresh_files` / `stale_files` / `refresh{ok, coverage, attempted_at}`；
  `py scripts/data_sync.py --report` 可见 `fresh=5635 stale=0 refresh=ok(100.0%)`。
  失败尝试只把 `refresh.ok` 置 false，不改 `updated_at`，避免把失败伪装成新数据。

## 2026-11-27 新股缺口补全 + 制度化（P-147）

### 已补

| 数据集 | 补充 | 结果 |
|---|---|---|
| `blocks` | 全市场 2025-12 以来缺映射的 **23 只新股**（688795 摩尔线程、688802 沐曦股份等）+ **689009**（科创板 CDR，自 2020 年即漏） | 5635 → **5659**；2025-12 以来新股缺口 **0** |
| `fundflow` | 002717 / 688121 / 689009 | 5222 → **5225** |

数据源：`blocks` 用 iFinD「证监会行业(2012版)大类」+ 巨潮「行业大类」双源，22/23 完全一致、
1 只单源兜底；行业名经**白名单校验**（必须已存在于仓内 84 种行业名），0 拒绝。
工具：`scripts/fill_missing_blocks.py`（幂等、默认 dry-run、`--write` 落盘、清单留证 `cache/blocks_fill_*.json`）。

### blocks 制度化改造清单

| # | 位置 | 状态 | 说明 |
|---|---|---|---|
| 1 | `lib/data/blocks_health.py`（新增） | ✅ 已做 | 成员级判据 `check_newstock_coverage`：上市 ≥10 自然日的 A 股必须在仓；清单按天缓存 |
| 2 | `scripts/check_blocks_newstock.py`（新增） | ✅ 已做 | 独立巡检 CLI，退出码 0 通过 / 1 有缺口 / 2 判据不可执行 |
| 3 | `scripts/data_sync.py::sync_blocks` | ✅ 已做 | 写仓后置告警（**不阻断**：旧映射仍有效，阻断会让整仓停更） |
| 4 | `tests/test_blocks_coverage.py` | ✅ 已做 | +5 条：缺口检出 / B 股忽略 / 最小天数过滤 / 清单不可用判红 / 目录缺失判红 |
| 5 | `scripts/fill_missing_blocks.py`（新增） | ✅ 已做 | 修复通道（判据与修复分离），带行业名白名单 |
| 6 | `sync_blocks` 分母（`_blocks_expected_count`） | ❌ 未改 | 快照仅 5580 只（不含 B 股/新股），改分母会误伤；成员级判据已覆盖该诉求，暂不动 |
| 7 | manifest 持久化 `missing_codes` | ❌ 未做 | 当前缺口清单只在 CLI 输出；如需仪表盘消费再补字段 |
| 8 | 98% 覆盖率阈值 | ❌ 未改 | 保持（防整批失败）；个体可见性交给成员级判据，两者互补不替代 |

### 剩余缺口（上游不可得，非本次遗漏）

| 数据集 | 缺口数 | 构成 | 原因 |
|---|---:|---|---|
| `fundflow` | 358 | 356 只北交所 + 000616（退市）+ 688496 | 东财资金流不覆盖北交所；688496 上游无数据 |
| `kline` / `kline_raw` | 各 4 | 920067/920309（北交所新股）+ 000616（退市）+ 689009（CDR） | 新浪/腾讯不覆盖北交所新股，本地 TDX 已删（ADR-0017） |
| `financial` | 2 | 000616（退市）+ 689009（CDR） | 同上游口径 |

> `000616` 已不在全 A 清单（退市），**不应补**；`201872` 等为 B 股，不计入 A 股口径。

## 按类别盘点

| 类别 | 本地位置 | 当前新鲜度 | 缺口 | 本次动作 |
|---|---|---|---|---|
| 个股行情 | `data/store/blocks`（5,635）、`snapshot`（7 日 / 38,827 行） | blocks 11-21 04:20；snapshot 11-26 19:00 | 行业分类 blocks 陈旧 | **刷新 blocks** |
| K 线 | `data/store/kline`（5,868 / 145.8 万行）、`kline_raw`（5,938 / 147.5 万行） | 11-26 19:07 / 17:31 | 前复权只有 500 只样本 | 不刷新（已最新） |
| 财务 | `data/store/financial`（5,579） | 11-21 09:00 | 全市场财务陈旧 5 天 | **刷新 financial** |
| 技术指标 | `bulk/07_integrated/{hs,hk,us}_technical.parquet` | 导出快照 11-24 | 无持续更新仓 | 记录缺口，暂不补 |
| 新闻 / 研报 / 公告 | `bulk/08_extra_market/{news,report,notice}_all.parquet` | 11-24 17:26 / 23:49 | 静态快照，无增量链 | 记录缺口，暂不补 |
| 板块 / 指数 / 宏观 | `data/store/index`（3 / 880 行，11-26 17:29）；`bulk/08_extra_market` 板块/指数/宏观 parquet | 指数最新；板块与宏观 11-24 | 宏观只有 `cn_core` 单指标 | 记录缺口，暂不补 |
| 资金流向 | `data/store/fundflow`（5,220） | 11-25 21:01 | 落后 1 天 | **刷新 fundflow** |
| 北向 / 南下 | `bulk/08_extra_market/north_all.parquet`、`south_all.parquet` | 11-24 06:41 | 未进数据仓，静态快照 | **刷新 north / south** |
| ETF / 热搜榜 | `bulk/08_extra_market/hot_{etf,stock,news,sector}.parquet` | 11-24 04:51 | 无历史序列；ETF 多维未落盘 | 记录缺口，暂不补 |
| 事件 / 龙虎榜 | `data/store/events`（10 / 95 行） | 11-26 17:32 | 行数少，覆盖有限 | 不刷新（已最新） |

## 本次执行顺序（受阻：会话锁被定时同步占用）

> 2026-11-26 19:10：`cache/data_sync.lock` 被每日 19:00 的 StoreDataSync 任务持有
> （PID 38604；kline 进度 580/5580）。该任务已包含 fundflow，但不含 blocks/financial。
> 处理：blocks/financial 与定时链的数据集**不相交**（定时链写 kline/kline_raw/index/
> snapshot/events/fundflow），故走直调函数路径刷新，manifest RMW 仍受 file_lock 保护；
> 不与定时链争抢同一数据集。north/south 继续排队，避免叠加网络负载。

1. [ ] `blocks`：全市场行业分类整批拉取并写仓
2. [ ] `financial`：全市场财务刷新（耗时最长，分批/断点）
3. [ ] `fundflow`：全市场资金流刷新（批量层优先）
4. [ ] `north / south`：通过 Go CLI 重抓北向/南下持仓并覆盖导出快照
5. [ ] 复核 manifest 与文件 mtime，回写本清单

## 重建判据（本次踩坑）

- 从 `_logs/{kind}.jsonl` 重建 parquet 时：**行数必须等于 `_ok=true` 的记录数**，
  不得按 `code` 去重（原表含多季度记录）；
- 必须保留 `_code` / `_market` / `_ok` 元数据列，否则下游契约漂移。

## 本轮补救项

- `fundflow`：359 只标的全部上游失败（`FundFlowUnavailableError`，东方财富 push2 连续失败后断开 300s），
  非本地代码缺陷；待上游恢复后对缺口名单重跑。
- ~~`blocks`：新映射 4,885 只，旧仓保留 5,635 个文件；差额 750 只需核实是否退市/代码变更后再清理。~~
  **2026-11-27 已核完并补齐**：0 退市，750 只全为瞬时刷新缺口；见上方复核节与 P-146。

## 长期缺口（待排期）

- 技术指标、新闻/研报/公告、板块/宏观、ETF/热搜均只有导出快照，没有持续更新链。
- 宏观指标仅 `cn_core`；Go CLI 已验证 `cn_gdp` / `cn_pmi` 可用，可补多指标历史库。
