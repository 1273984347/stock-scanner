# 新 Agent 启动 Prompt（可直接发送）

> **用途**：新 session 启动时，将下方【正文】整段复制发给新 Agent，快速完成项目认知 + 规则约束。
> **当前 HEAD / 下一 W / 待办**：见 [交接文档.md](交接文档.md)「零 / 一」——本文件不维护版本清单，避免过期。
> **维护契约（防乱写）**：本文档是「速用精简版」payload，只允许结构性编辑——① 保持【正文】五段骨架不变；② 新增/变更规则必须**就地替换**旧表述并全篇去重；③ 禁止追加「W### 速记 / 治理要点」类历史段（历史一律写入 [CHANGELOG.md](CHANGELOG.md) 与交接文档「一 / 三」）；④ 涉及门禁/计数口径时，全篇只保留一份现役值；⑤ 与交接文档/CHANGELOG 冲突时以后两者为准。
> **修订**：2026-08-26 创建（六文档治理体系落地批次）。

---

## 正文（复制以下内容发送给新 Agent）

你是 Stock Scanner（A 股个人量化选股系统）项目（D:\Claude Code\workspace\projects\stock-scanner）的新接任 Agent。本项目由多 session / 多 Agent 协作开发，有严格的文档同步与验证规则。请按以下顺序了解项目并遵守规则：

【第 1 步 · 定位现状（必读 4 份）】
1. 交接文档.md →「零、当前阻塞」+「一、当前进度」：当前 HEAD、下一 W 编号、遗留阻塞/待办
2. CHANGELOG.md → 时间线：W### 批次四件套（来源/文件/验证/状态），判断「改了什么」唯一依据
3. AGENTS.md → 项目全貌：定位/技术栈/目录/工作流/验证分层/铁律
4. README.md → 项目说明与现役版本行

【第 2 步 · 文件管控（动手前必读）】
5. 各文档「维护契约」头注：可编辑边界 + 禁改段（历史版段/归档/.env/门禁脚本）
6. `.env` 含敏感 Token（TUSHARE_TOKEN/FEISHU_*/API_SECRET_KEY），禁止 commit、禁止写入任何文件；gitleaks pre-commit 会对提交自动扫描
7. 交接文档.md「三、方法论沉淀」：可复利经验（E1 铁律、DRL 三 skill 闭环等），必须遵循

【第 3 步 · 结构与历史（深挖时）】
8. STRUCTURE.md → 目录结构（lib/ 110+ 模块分组）
9. docs/superpowers/ → spec / plan / retrospective 开发档案（历史方案与复盘）
10. CHANGELOG.md 里程碑段 → 项目演进脉络

【第 4 步 · 干活铁律】
- 开发流程：需求 → 验证 → 实现 → 回测 → 记录；策略/因子修改必须回测验证 + Walk-forward
- 数据源修改：必须测试多数据源降级链（Tushare→AkShare→WeStock→通达信→Baostock→东财）
- 每个文件修改后：Grep spot-check 验证落地（E1 铁律：声明 ≠ 落地，禁止假收敛）
- 验证分层：L0 `python -m py_compile` → L1 `pytest tests/ -v`（全绿才可提交）→ L2 `python run.py --help` → L3 `uvicorn api:app --reload`（API 变更时）
- 提交：conventional commits（feat:/fix:/perf:/docs:/chore:）+ 中文描述；改完 CHANGELOG 现役批次「文件/验证/状态」行同步更新
- Windows 本机：PowerShell 无 heredoc（多段 message 用多个 `-m`）；内联 `python -c` 引号易碎用临时 .py 文件
- API 安全：新增端点过安全 Checklist（输入校验→认证→鉴权→错误消息→资源限制）
- Bug 调试 > 10min：停止猜测，走系统化排查 + 沉淀经验

【第 5 步 · 现役口径（唯一版本，勿信旧速记）】
- 当前版本：**v1.3**（现役批次以 [交接文档.md](交接文档.md)「一」为准，写死值会过期——每次都读）
- 当前 W：**W002（A 股周期顶部预警，进行中）**；新批 W 编号先 Grep CHANGELOG 取 max+1
- 数据源优先级：Tushare (主) → AkShare → WeStock → 通达信 → Baostock → 东方财富（财报链：东财 datacenter → 新浪 → 东财逐期）
- 测试基线：`pytest tests/` 0 failed（具体数量以当次输出为准）
- TDX 数据路径：`D:/TDX/vipdoc`（`TDX_DATA_PATH`）；更新用 `scripts/update_tdx_data.py`
- 更早批次细节与历史坑：一律查 CHANGELOG 与交接文档「三、方法论沉淀」，本文件不内嵌历史速记