# 数据新鲜度与缺口清单

> 盘点时间：2026-11-26 19:10。公开仓不含数据文件；以下均为本地数据。
> 数据源层级：`data/store/` = 项目内持续更新仓（manifest 记账）；`data/exports/bulk/` = 一次性全市场导出快照。

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

## 本轮补救项

- `fundflow`：359 只标的全部上游失败（`FundFlowUnavailableError`，东方财富 push2 连续失败后断开 300s），
  非本地代码缺陷；待上游恢复后对缺口名单重跑。
- `blocks`：新映射 4,885 只，旧仓保留 5,635 个文件；差额 750 只需核实是否退市/代码变更后再清理。

## 长期缺口（待排期）

- 技术指标、新闻/研报/公告、板块/宏观、ETF/热搜均只有导出快照，没有持续更新链。
- 宏观指标仅 `cn_core`；Go CLI 已验证 `cn_gdp` / `cn_pmi` 可用，可补多指标历史库。
