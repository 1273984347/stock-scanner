# RF-01 踩坑集（工程失败模式与推广）

> **来源**：2026-11-07 RF-01 分阶段重构（Phase 0 → Phase 3）的真实执行过程。
> **范围**：只收录**本项目实际踩到过**的坑，每条都带可复算证据；不含"可能会踩"的泛泛而谈。
> **规模**：**78 条**（P-01～P-78），分八类。
> **姊妹文档**：执行过程与逐批验收见 `docs/plans/2026-11-07-refactor-plan.md` 第七节；
> 基线读数见 `docs/refactor/baseline.md`；尺子口径见 `docs/refactor/README.md`。

---

## 零、怎么用这份文档

| 场景 | 查这里 |
|---|---|
| 正在写**门禁 / 基线 / 对拍**工具 | 第一章（尺子会怎么骗你） |
| 正在改**跨平台文件写 / 并发 / 锁** | 第二章 |
| 正在写**mock / fixture / 禁网** | 第三章 |
| 正在**搬代码 / 合并同类实现** | 第四章 |
| 正在碰**数据源 / 回测 / as_of_date** | 第五章 |
| 正在改 **CI 矩阵 / 触发器 / 额度** | 第六章 |
| 正在**用脚本改代码 / 提交** | 第七章 |
| 正在做**收官核验 / 对照实验** | 第八章 |

每条统一格式：**现象 → 根因 → 修复 → 推广**。「推广」一栏是本文档的价值所在：
这些坑在别的项目、别的技术栈会以别的面目重演，**判据比结论更可迁移**。

---

## 索引

| 类 | 编号 | 主题 | 一句话 |
|---|---|---|---|
| 一 · 度量与门禁 | P-01 | 数字对得上 ≠ 集合对得上 | 正则数端点与真实口径**巧合相等** |
| | P-02 | 空字段伪装成完整基线 | 恒为 null 的字段比缺字段更危险 |
| | P-03 | 尺子自己不稳定 | 合成数据用了带随机盐的 `hash()` |
| | P-04 | 基线必须来自门禁执行环境 | 本地 43.09% vs CI 43.77% |
| | P-05 | dtype 是实现细节不是行为契约 | 跨 pandas 版本假红 6 处 |
| | P-06 | 覆盖率不能跨环境比 | 本地自查 CI 基线 ⇒ 10 模块误报 |
| | P-07 | 测量前必须先关日志 | 日志 I/O 放大约占 62% |
| | P-08 | 漏斗的最后一步必须全覆盖实测 | 静态 grep 漏掉头号慢源 |
| | P-09 | 稳定性判据用 max/min，不用小样本 CV | CV=0.0038 但 max/min=1.01 |
| | P-10 | 尺子必须自证会判红 | 永不触发的判据等于没有判据 |
| 二 · 跨平台与并发 | P-11 | Windows 不许 `os.replace` 覆盖打开的文件 | 波及 31 个用例 |
| | P-12 | 只写内存副本 ⇒ 后写者覆盖先写者 | 5 进程 × 100 条只剩 100 条 |
| | P-13 | `os.replace` 偶发失败 | 退避用 `threading.Event().wait()` |
| | P-14 | 过滤/截断必须在取窗口**之前** | 先 tail 再截断 = 少取 |
| | P-15 | bash 续行符在 Windows runner 直接炸 | 别依赖 shell 差异 |
| | P-16 | Windows CI 的 stdout 不是 UTF-8 | 中文 print 直接崩门禁 |
| 三 · 测试基础设施 | P-17 | `patch.dict("sys.modules")` 会撤销作用域内的新 import | matplotlib 半初始化 ⇒ KeyError packaging |
| | P-18 | patch 模块级 import x 无效 | 改已绑定的模块属性 |
| | P-19 | patch 目标必须是**使用方** | 改定义方无效 |
| | P-20 | 被 skip 的测试等于没测 | skipif(win32) 藏了 3 个真 bug |
| | P-21 | 默认禁网必须放行 loopback 与 AF_UNIX | 挡太死会打断 event loop 创建 |
| | P-22 | 异常类型要匹配真实失败 | 抛 RuntimeError 改变了被测分支 |
| | P-23 | 同一类遗漏在同一文件内也会漏 | 逐个补是治标，要改默认机制 |
| 四 · 代码搬迁与等价性 | P-24 | 搬函数必须逐字复制 | 凭记忆重写出现 4 处偏差 |
| | P-25 | 路径基准随文件位置变化 | Path(__file__).parent 搬进 lib/ 就偏了 |
| | P-26 | 搬走函数体后必须删旧定义 | 后定义者覆盖 import 的名字 |
| | P-27 | 别名必须绑定同一对象，且禁止重新赋值 | 状态分裂 |
| | P-28 | 同名函数 ≠ 副本 | 6 个 _to_float 语义不同 |
| | P-29 | 特征测试要在合并前抓等价性破坏 | 当场抓出 3 处不等价 |
| | P-30 | 测试里复制实现会与真身漂移 | 引用模块真身 |
| | P-31 | 计划里的判断要当场复核 | 伪需求当场纠正 |
| | P-32 | 文件名与目录名冲突 | api.py 与 api/ 不可共存 |
| | P-33 | 模块 import 期副作用 | 加默认关闭的 env 开关 |
| 五 · 数据语义与前视偏差 | P-34 | 查前视偏差不能只看签名与文档 | adata 收了参数却仍用 now() |
| | P-35 | 参数透传不完整 | registry 里 3/8 源没透传 |
| | P-36 | 少取也是前视偏差 | 少取比多取更隐蔽 |
| | P-37 | 合并同类实现会静默改变财务语义 | "0" → None vs 0.0 |
| | P-38 | PEP 562 懒加载使属性级 monkeypatch 失效 | 抽纯函数来测 |
| 六 · CI 与额度治理 | P-39 | job 级 if 不能引用 matrix.* | workflow 直接失败、job 列表为空 |
| | P-40 | Actions 额度算术 | 9 job ≈ 117 分钟；3 job ≈ 39 |
| | P-41 | workflow 文件级错误不消耗 job 分钟 | 可以放心试错 |
| | P-42 | 门禁结论取**步骤级** | 整体 conclusion 被既有红淹没 |
| | P-43 | commit 失败后 push 仍会执行 | PowerShell 对 native command 不生效 |
| | P-44 | 内容哈希会触发 secret 扫描 | 用精准 regex allowlist |
| 七 · 工具链与编码纪律 | P-45 | 编辑前必须先读原文 | 凭记忆匹配失败 |
| | P-46 | 模板里的双花括号与换行需转义 | 多次产出语法错 |
| | P-47 | 生成代码的脚本必须先在小样本上验证 | 双重包裹 bug |
| | P-48 | git add 用显式 pathspec | -A 会带入 backups/、skills/ |
| 八 · 收官核验与实验纪律 | P-49 | 对照实验用 checkout 还原会污染工作树 | 静默覆盖未提交改动 |
| | P-50 | 覆盖率下降可能是修复的正面结果 | 补测试而非豁免 |
| | P-51 | 门禁静默跳过基线缺失模块 = 盲区 | 必须显式告警 |

---

## 一、度量与门禁：尺子本身会骗你

本章的全部教训可以用一句话概括：**尺子不是天然可信的，尺子要跟被测对象一样接受验证。**

### P-01 · 数字对得上 ≠ 集合对得上

**现象**：RF-01 计划里的"68 个 API 端点"是用
`grep -c '@app\.(get|post|put|delete)' api.py` 得来的。建好契约快照工具后一比，
`/api/*` 真实路由数**也是 68** —— 数字完全吻合，但**集合不同**。

**根因**：grep 口径含 3 条非业务路径（`/`、`/favicon.svg`、`/sw.js`），
且**漏掉** `PATCH /api/alerts/rules/{rule_id}`（PATCH 不在正则里），还漏 WebSocket `/ws`。

**修复**：端点数一律用 `scripts/contract_snapshot.py` 的**运行时口径**产出；
校验必须"成员 + 数量"**双校验**，**数量相等不能单独作为通过条件**。

**推广**：**任何"计数"结论，只要两个独立口径得出同一个数，就要警惕它们其实量的是不同的东西。**
巧合相等是最危险的一类错误 —— 它让所有基于该数字的核对"看起来通过"。
判据：拿到计数后，必须至少做一次**集合级 diff**，而不只是比对数字。

**证据**：快照共 **72** 条路由（GET 52 / POST 15 / DELETE 3 / PATCH 1 / WEBSOCKET 1），
其中 `/api/*` **68**、非 `/api` HTTP **3**、WS **1**；
`app.routes` 共 **78**（含 Mount 2 + 框架内置 4）。

---

### P-02 · 空字段伪装成完整基线

**现象**：契约快照工具初版产出的 JSON 里，`status_code` 与 `security_schemes` **恒为空**。
但因为字段"都在"，基线看起来是完整的。

**根因**：
- `status_code`：从 openapi 取键时方法名大小写写错（openapi 用小写 get，代码用大写）⇒ 恒 null。
- `security_schemes`：本项目用 `APIKeyHeader` + `Depends` 链，
  **不使用** FastAPI 的 `SecurityRequirement` ⇒ `dependant.security_requirements` 恒为 `None`。

**修复**：改为按 method 小写键逐个提取 `status_code`；鉴权改记 `auth_dependencies`（依赖名），
并在快照 meta 写明鉴权机制；**对每个字段做非空率统计**（null 数 / 空 dict 数），异常即字段级缺陷。

**推广**：**基线里的空字段是伪装成完整的漏洞** —— 它让"字段都在"看起来像"基线可靠"。
凡是"新产出的一组字段"，第一件事不是看总量，而是看**每个字段的非空率**。
空值率异常就是字段级 bug，不是真实读数。

---

### P-03 · 尺子自己不稳定

**现象**：双跑对拍框架首次自对拍，diff 不为 0 —— `score.factors.adx` 出现 63.9 vs 15.9 等 **1000 处差异**。
框架刚写完就自己红了。

**根因**：合成数据用 `abs(hash(code)) % 9973` 作种子，而 **Python 内置 `hash()` 对字符串带随机盐**
（`PYTHONHASHSEED`）⇒ 每个进程合成的数据都不同 ⇒ 对拍必然假红。

**修复**：改用 `zlib.crc32(code.encode())`（确定性、跨进程、跨平台一致）。

**推广**：**凡需要跨进程/跨环境复现的合成数据，种子绝不能用内置 `hash()`。**
需要确定性哈希时用 `zlib.crc32` / `hashlib`。
自检方式：同一份代码连跑两次，输出必须**逐字相同**。

---

### P-04 · 基线必须来自门禁执行环境

**现象**：覆盖率门禁第一版用**本地** win32 数字 43.09% 作基线。
原设计理由是"三平台分支覆盖不同 ⇒ 按平台存基线"。

**根因**：**这个归因被实测推翻了。** 三平台 CI 覆盖率几乎相同（43.77 / 43.80 / 43.81），
平台相关分支目前并未造成覆盖差异；真正的差异是**本地 Windows 43.09% vs CI 43.77%（差 0.68pp）** ——
本地有 TDX 数据与 cache，走到了不同的分支。

**修复**：第一版本地基线**已移除**，改由 CI 重建；本地自查必须用本地基线。

**推广**：**凡"基线 / 阈值"类判据，先量「同一环境 vs 不同环境」的差，再决定口径。**
想当然的归因（"肯定是平台差异"）会被数据推翻 —— 而且会让基线建在错误的环境上，
从此每个真回归都被噪声淹没。
配套：**覆盖率基线按平台存**，因为三平台数字已出现微小差异（darwin 43.81 vs win32 43.77）。

---

### P-05 · dtype 是实现细节，不是行为契约

**现象**：对拍框架在 CI 首轮验证报出 6 处差异，逐个查下去：**数值与字段内容完全一致**，
只有 dtype 表示不同 —— 本地 Windows pandas 推断为 `object`，CI Linux pandas 推断为 `str`。

**根因**：pandas 对字符串列的 dtype 推断在版本间不同。dtype 属于**实现细节**，不属于对外行为契约。

**修复**：新增 `_semantic_dtype()` 归一为语义类型（object/str/string → string；float* → float；
int*/uint* → int）。**真正的类型变化（float → string）仍然会被检出**，
只是不再把"object 还是 str"当成行为漂移。

**推广**：**跨环境比对要比"语义"，别比"实现细节"。**
同类：序列化格式（JSON vs YAML）、浮点表示（0.1+0.2 vs 0.3）、日期时区标注。
判据：问自己一句 —— **这个差异如果变了，用户会感知到吗？** 不会的就不该进契约。

---

### P-06 · 覆盖率门禁不能跨环境比

**现象**：本地跑覆盖率门禁，**FAIL**，报 10 个模块回归（`audit_logger` −27pp 等）。

**根因**：本地 43.33% vs CI 基线 43.77%，且**本次改动的 api.py 与 opportunity_scorer.py 都不在回归列表里**
⇒ 与本次改动无关。差异来自本地无 TDX 数据 ⇒ 某些代码路径不可达。

**修复**：覆盖率基线只能与**同环境**的基线比。工具加 `--allow-modules` 供"代码在模块间搬家"时豁免
（分母变化属结构变化，不是回归）；≥5 个模块同时回归时打印 `[WARN]` 提示跨环境比较。

**推广**：**门禁的阈值只在产生它的那个环境里有意义。**
配套：覆盖率对"抽离状态"这类改动几乎中性（搬走的代码原本被覆盖、新模块未被覆盖，净变化仅 +0.01pp）
⇒ **搬代码不等于降覆盖率**，门禁不会误报这类重构。

---

### P-07 · 测量前必须先关日志

**现象**：首次采集 pytest 耗时跑了 **13 分钟仍卡在 13%** 处，输出被 DEBUG 日志淹没。

**根因**：`pytest.ini` 的 `log_cli=true` + `log_cli_level=DEBUG`，叠加 `tests/logging.conf` 的
`logger_quant`（level=DEBUG → stdout）⇒ **DEBUG 日志双份打到终端**；
matplotlib 的 `font_manager` 对每个字体逐条打分被逐条放大。

**修复**：工具一律带 `-o log_cli=false -o log_cli_level=WARNING`。关日志后同一集合仅 **635.37s（10.6min）**。

**推广**：**任何耗时测量都要先确认日志 / 输出通道没有放大效应，否则结论无效。**
判据：若"日志开着测出来的慢"占大头，那测的是日志不是被测代码。
配套：`log_cli=true` 这类配置应该只活在开发机，不该在需要稳定读数的环境里默认开启。

---

### P-08 · 漏斗的最后一步必须全覆盖实测

**现象**：全量采集两次都在 13% 处中止（预计 ~100min）。改用三步漏斗：
静态 grep 圈候选 → 候选精测 → 文件级全覆盖扫描。

**根因**：**第一轮的静态 grep 漏掉了 4 个慢文件**，其中 `tests/test_scorecard.py`（**304.65s，占总时长 47.9%**）
是头号慢源 —— 它文件内**不直接出现** sleep / requests / akshare 关键词，只是**间接**调用 `lib/scoring/scorecard.py`。

**修复**：最后一步必须是**全覆盖实测**（`--scan-by-file`），不能停在静态推断。

**处置后的实测**：该头号慢用例 `tests/test_scorecard.py::test_score_structure` 单例耗时
**188.07s → 0.04s**（退避常量收口后，见 P-23）。

**推广**：**静态搜索只能用来缩小候选范围，不能用来下"就这些了"的结论。**
间接调用链会让静态检索系统性漏掉最大的那一块。
判据：漏斗的最后一格必须是"全量跑一遍"，不是"我搜过的都跑了"。

---

### P-09 · 稳定性判据用 max/min，不用小样本 CV

**现象**：2–3 轮的小样本里，CV 算出来忽大忽小，不能用来区分"确定性慢"和"网络抖动"。

**根因**：小样本 CV 方差极大。

**修复**：改用 **max/min 比值**。实测：确定性退避用例 max/min = **1.01**；真实网络抖动用例 = **1.42 / 1.43**。
另有一个 CV 0.0038、max-min 1.01 却要 17.08s 的用例 ⇒ 稳定但慢，是**确定性退避**而非网络抖动。

**推广**：**样本量小时用比值，不用方差类统计量。**
判据：max/min ≈ 1 ⇒ 稳定；显著大于 1 ⇒ 有抖动源。
另：耗时读数必须**区分冷/热缓存** —— 同一份代码首轮 21.26s、复跑 9.80–11.13s，
换顺序复跑仍一致 ⇒ 差异来自缓存而非执行顺序 ⇒ 报告要给区间并说明冷热。

---

### P-10 · 尺子必须自证会判红

**现象**：一个从未被证明会判红的门禁，可能只是个**永不触发的空判据**。

**修复**：契约门禁做了三路自证：

| 验证 | 操作 | 期望 | 实测 |
|---|---|---|---|
| A 合成漂移 | 污染基线副本：async 翻转 + 删端点 + 加端点 | 判红 | ✅ 检出 3 处（ADDED / REMOVED / CHANGED） |
| B **真实扰动** | api.py 临时把 /api/health 由 async def 改为 def | 判红 | ✅ 检出 async: True → False，文件字节级还原 |
| C 还原回归 | 还原后重跑 --check | PASS | ✅ exit 0 |

对拍框架同样自证：扰动基线 2 处 ⇒ 检出并定位（`score.factors.adx 999→47.19`、
`backtest.exit_reasons.stop_loss 42→1`），exit 1；重新 `--capture` 后 exit 0。

**推广**：**任何"我加了门禁"的声明，都要附一句"它能判红吗"和判红证据。**
一个恒绿的门禁比没有门禁更危险 —— 它给人虚假安全感。
自证必须包含**真实扰动**（改一行真代码），不能只有合成基线污染。

---

## 二、跨平台与并发：同一份代码三种行为

### P-11 · Windows 不允许 `os.replace()` 覆盖一个仍被打开的文件

**现象**：原实现"用文件锁锁住**目标文件** + 原子替换**同一个目标文件**"在 Windows 上**必然**
`PermissionError [WinError 5]` ⇒ 波及 **31 个用例**
（`position_manager._save` → partial_exit / position_manager / position_constraints；
`signal_tracker._save_history` → investor_agents / panel_decision）。POSIX 允许，所以只在 Windows 暴露。

**根因**：Windows 的文件打开语义禁止替换仍被持有的文件句柄；POSIX 允许 unlink 已被打开的文件。

**修复**：**锁伴生 `.lock` 文件，不锁目标文件本身** ⇒ 目标文件可自由被原子替换，跨平台语义一致。

**推广**：**凡"加锁 + 原子替换"组合，先问一句：锁住的是不是那个要被替换的文件？**
跨平台写文件的标准姿势是「锁伴生文件 → 写临时文件 → `os.replace`」。

---

### P-12 · 只写内存副本的多进程写 ⇒ 后写者覆盖先写者

**现象**：并发测试 `test_concurrent_save_history_no_data_loss` 失败：
5 进程 × 100 条，磁盘上**只剩 100 条，丢了 400 条**。

**根因**：文件锁只保护了"写"这一段，而"读磁盘 → 改 → 写"是**三段式** ⇒
每个进程基于自己内存里的旧副本写回，后写者覆盖先写者。
（这正是原注释"api.py 4 endpoint + cron 5 进程抢写"描述的真实风险场景。）

**修复**：**整个 read-modify-write 放进锁内** —— 加锁 → 读磁盘最新 → 合并去重 → rotation → 原子写回。
合并时**已结算字段优先**（避免覆盖其它进程刚回填的收益率）。

**推广**：**"加了文件锁"不等于"并发安全"。**
必须问：锁的粒度是否覆盖了完整的 read-modify-write 临界区？
配套：并发测试必须**同时断言结果与条数**，只断言"不抛异常"会让丢数据完全隐形。

---

### P-13 · `os.replace` 在 Windows 上会偶发失败

**现象**：即使改成伴生锁文件，Windows 上 `os.replace` 仍偶发失败。

**根因**：外部句柄占用 —— 杀毒软件实时扫描、索引服务、编辑器等会在瞬间持有目标文件。

**修复**：加 `_atomic_replace` 重试（10 次）。**退避用 `threading.Event().wait()` 而非 `time.sleep`** ——
后者会被测试的"默认零退避"autouse fixture 短路掉，导致重试窗口失效。

**推广**：**重试逻辑的退避方式要考虑可被测试短路。**
配套：`os.replace` 失败在 POSIX 上几乎不出现 ⇒ **这类问题本地 Linux 永远测不出来**，
必须在 Windows 或 CI 矩阵上验证。

---

### P-14 · 过滤 / 截断必须在取窗口**之前**

**现象**：修 `as_of_date` 时若先 `tail(days)` 再按目标日期截断，"最近 N 天"窗口里可能根本不含目标日期 ⇒ **少取数据**。

**根因**：截断与取窗口的顺序反了。

**修复**：过滤逻辑抽成 `lib.data_protocol.truncate_to_date`（单一实现 + 可测），并在 `tail(days)` **之前**过滤。

**推广**：**前视偏差不只有"多取"一种形态，"少取"同样是偏差，而且更隐蔽** ——
少取不会让回测曲线好看到可疑，只会让结果"有点怪"。
判据：任何"取最近 N 条 + 截断到某日"的组合，先画时间轴再写代码。

---

### P-15 · bash 续行符在 Windows runner 上直接炸

**现象**：workflow 里用反斜杠续行的多行命令，在 Windows（默认 shell = PowerShell）报 `ParserError`，
pytest 根本没跑起来 ⇒ 该 job 失败。

**修复**：**跨平台 workflow 一律用单行命令**（或显式 `shell: bash`），不依赖 shell 差异。

**推广**：CI YAML 里的多行命令是**跨平台隐患**，尤其是路径、管道、续行符混用时。
判据：写 workflow 时问一句 —— **这段在 PowerShell 下能跑吗？**

---

### P-16 · Windows CI 的 stdout 不是 UTF-8

**现象**：门禁脚本里 `print("[PASS] 契约无漂移…")` 在 Windows CI 抛
`UnicodeEncodeError: 'charmap' codec can't encode characters`；本地终端是 UTF-8 ⇒ **本地永远测不出来**。

**修复**：双保险 —— ① 全部门禁输出改**纯 ASCII**（中文说明留在文档与代码注释里）；
② 入口处 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`。

**本地复现**：

```powershell
`env:PYTHONIOENCODING='cp1252'
python scripts/contract_snapshot.py --check   # 修复前崩溃；修复后 exit 0
```

**推广**：**凡"要进 CI 的工具 / 脚本"，输出默认 ASCII。**
中文输出在本地永远正常，只有真实 CI 才暴露 —— 这类 bug 本地无法通过任何测试发现。

---

## 三、测试基础设施：mock 打在错的地方

### P-17 · `patch.dict("sys.modules", ...)` 会撤销作用域内新 import 的模块

**现象**：CI 上 `KeyError: 'packaging'`，栈为
`matplotlib/__init__.py → from packaging.version import parse → importlib._bootstrap → KeyError`。
**三平台轮流偶发**、只在带 coverage 时出现、换顺序也会变。

**根因**：`unittest.mock.patch.dict` 进入时对目标字典做快照，**退出时执行 `clear() + update(快照)`**
⇒ **作用域内新 import 的模块会被整体删除**（不只是被 patch 的那个 key）。
若 matplotlib 恰好在某次 patch.dict 作用域内**首次 import**，它处于**半初始化**状态却被移出 `sys.modules`
⇒ 之后再 import 会抛 "cannot load module more than once per process"（本地）或 `KeyError: 'packaging'`（CI）。

**修复**：在 `tests/conftest.py` **模块顶层**预载 `import matplotlib` ——
此时任何 patch.dict 作用域都还没开始，其快照已含 matplotlib / packaging ⇒ 退出时 clear+restore 会**保留**它们。

**推广**：**凡测试里用 `patch.dict("sys.modules", ...)` 模拟模块 ⇒ 被测路径上的重模块 / 子模块 import
要么在 conftest 顶层预载，要么在 patch 之外完成。**
否则会出现"本地能过、CI 偶发红"且极难定位。
配套：**覆盖率只是放大器**（import 时序窗口变长、概率上升），不是根因 ——
遇到"加了 coverage 才偶发"要先查并发 / 时序。

**如实记录的弯路**：
① 先在 CI 里预热 matplotlib 字体缓存 ⇒ **无效**（预热在 pytest 进程外，
进程内 `sys.modules` 仍会被撤销）⇒ 已移除；
② 用 `grep -n 'packaging' matplotlib/` 排查时把条件写成 packaging **且** metadata 的 AND
⇒ 漏掉了 `matplotlib/__init__.py` 里的 `from packaging.version import parse`
⇒ **grep 多条件 AND 会漏，须逐条件分别搜。**

---

### P-18 · 测试 patch `sys.modules` 对模块级 import x 无效

**现象**：想 mock portalocker，mock 永远不被调用 ⇒ 断言必失败。

**根因**：`lib/scoring/signal_tracker.py` 是**模块级** `import portalocker` ⇒ 已绑定到模块属性，
改 `sys.modules` 里的 key 改不动那个已绑定的引用。

**修复**：改用 `patch("lib.signal_tracker.portalocker", mock_pl)`。

**推广**：**patch 的目标是"使用方模块的属性"，不是"被导入的模块"。**
判据：`from X import f` 与 `import X` 都已把引用绑到使用方模块上。

---

### P-19 · patch 目标必须是**使用方**

**现象**：对拍框架 patch `lib.strategies.get_zt_candidates` **不生效**，真实策略被执行并报错。

**根因**：`lib/strategy/pipeline.py:23` 是模块级 `from lib.strategy.strategies import (...)` ⇒ 已绑定引用。

**修复**：必须 patch `lib.pipeline.get_zt_candidates`。

**推广**：**改"定义方"无效，要改"使用方"的引用。**
与 P-18 同源：都是"import 时绑定"造成的。
配套：框架必须记录 **traceback**（不只存类型 + 消息）——
只存类型和消息定位不到位置，加上 `traceback.format_exc()` 后一眼看到
`lib/strategy/pipeline.py:841 regime_scalar[1]` 期望 (min, max) 二元组却给了 float。

---

### P-20 · 被 skip 的测试等于没测

**现象**：并发测试有 `skipif(win32)`（理由："Windows 会 PermissionError"）⇒ 该修复一直没被验证。

**修复**：P-11 修掉根因后，skipif 随之失效即可移除；移除后又立刻暴露 P-13 / P-22 等新问题。

**顺带暴露的慢因**：并发用例移除 skip 后耗时 **69.54s** —— 根因是
`record_signal` 未传 date 时**每次都查 akshare 交易日历**。worker 内固定日期后 **2.30s**（30×）。
⇒ **推广：并发 / 性能类测试要同时断言「结果正确」与「耗时合理」**，否则慢会被当成环境问题而放过。

**推广**：**skip 是测试覆盖率的黑洞。** 写 skip 理由时必须写清"什么条件恢复后要重跑"；
修掉那个条件后，**同批次把 skip 撤掉**。
判据：看到测试文件里有 skipif，先问"这个条件现在还成立吗"。

---

### P-21 · 默认禁网必须放行 loopback 与 AF_UNIX

**现象**：无差别挡 socket 会让 **event loop 创建失败**，
报 `'ProactorEventLoop' object has no attribute '_ssock'` ⇒ 连带**所有用 FastAPI / httpx TestClient 的测试全挂**。

**根因**：Windows 上 asyncio 的 `socket.socketpair()` 是用**真实 connect() 到 127.0.0.1 模拟实现的**
（`lsock.bind` + `csock.connect`）⇒ 挡掉它 event loop 就建不起来。
（Linux / macOS 上 multiprocessing 的 socketpair 走 AF_UNIX，挡掉会影响多进程。）

**修复**：默认禁网 fixture **放行 loopback 与 AF_UNIX**，只挡真实外部连接与 DNS。

**推广**：**"禁网"这类全局拦截必须留出基础设施通道**（本地回环、IPC、unix socket）。
判据：拦截规则按**目标地址 / 协议族**分层，不要按"是不是 socket"一刀切。

---

### P-22 · 异常类型要匹配真实失败

**现象**：禁网时抛 `RuntimeError`，导致下游"断网降级"分支**根本没被走到**。

**根因**：requests / urllib3 会把 socket 的 `OSError` 包装成自己的 `ConnectionError`。

**修复**：禁网时应抛 **`ConnectionError` 子类**（而非 `RuntimeError`），才能让被测代码走真实断网路径。

**推广**：**mock 的异常类型必须与真实失败一致，否则你测的是另一条分支。**
判据：先确认"真实断网时下游怎么反应"，再决定 mock 抛什么。

---

### P-23 · 同一类遗漏在同一文件内也会漏 ⇒ 要改默认机制，不是逐个补

**现象**：实测**同一个文件里 3 个用例都正确 patch 了退避 / 超时常量，唯独第 4 个漏了**；
另一个文件因退避是**硬编码**（连常量都 patch 不到）而漏。

**根因**：缺的不是"补丁"，是**默认机制**。逐个补是治标。

**修复**：引入 autouse fixture 统一把退避压到 0.01。

**推广**：**发现第 2 处同类遗漏时，就不要再逐个补了，改默认机制。**
配套三条可组合性结论：
- monkeypatch 的撤销栈是 **LIFO** ⇒ 后应用的 patch 覆盖先应用的
  ⇒ autouse fixture 先执行、测试内的显式 `with patch(...)` 后应用 ⇒ **优先生效**
  ⇒ 可以安全地用 autouse 设"默认零退避"，同时让专门测该行为的用例用自己的显式 patch 断言真实秒数。
- **patch 一个模块导入的标准库符号 = patch 全局**：本例 `lib/data_layer.time` 就是标准库 time 模块本身
  ⇒ patch 它的 sleep 一处覆盖 data_layer / data_margin / data_baostock / sentiment 的全部退避。
  好处是一处覆盖多处，代价是影响面更大 ⇒ **必须跑全量验证零新增失败**。
- **配套实验**：引入全局 fixture / 拦截层的改动，必须跑一次"回退该改动的同口径对照"
  （`git stash` → 跑同一批 → 还原），否则无法区分"新引入的失败"与"既有失败"。

**附带收益（实测）**：本地全量 pytest 从 >28min → 约 11–13s（>100×，2026-11-07 复测 13.02s）；
CI 上 110.69s → 33.90s（3.3×）→ **15.69s**（累计 7.1×）；残余慢点（≥3s）全部清零。

---

## 四、代码搬迁与等价性

### P-24 · 搬函数必须逐字复制

**现象**：把缓存层搬到新模块时，凭记忆重写，出现**四处偏差**：解压逻辑、压缩分支、chmod、logger 级别。

**根因**：凭记忆"重写等价代码"是不可靠的 —— 看起来一样的东西往往差在边界分支上。

**修复**：搬函数 / 常量**读原文复制**。

**推广**：**搬迁是复制，不是重写。**
判据：搬完做逐字 diff，不是"读一遍觉得对"。

---

### P-25 · 路径基准随文件位置变化

**现象**：`_perf_file = Path(__file__).parent / "cache" / "perf_baseline.json"` ——
从根目录搬到 `lib/` 后，`__file__` 变了 ⇒ **落盘目录改变** ⇒ 行为漂移。

**修复**：新模块内用 `parent.parent`，并在 docstring 写死该约束。

**推广**：**搬迁时凡是"相对当前文件"的路径都要重算。**
配套：`Path(__file__).parent / "cache"` 这类基准，搬到子目录后必须复核。

---

### P-26 · 搬走函数体后必须删旧定义

**现象**：`opportunity_scorer.py` 里旧的 get/set/reset_opp_score_weights 函数体若保留，
**后定义者覆盖 import 的名字** ⇒ 旧实现会引用已迁走的 `_active_weights` ⇒ 运行时 NameError。

**修复**：用 AST 定位函数的 `lineno/end_lineno` **精确删除源码块**，比正则可靠。

**推广**：**"复制过去"之后必须确认"删掉原来"** —— Python 的后定义覆盖是静默的。
判据：搬完在旧文件里 grep 函数名，确认没有第二份定义。

---

### P-27 · 别名必须绑定同一对象，且抽离后禁止重新赋值

**现象**：`from lib.infra.perf_metrics import _perf_metrics` 绑定的是**列表对象**
⇒ 原地 append / clear 两边可见；但若抽离后任何一处写 `_perf_metrics = []`
⇒ **状态分裂**（api.py 与新模块各有一份）。

**修复**：模块 docstring 写死"只能原地操作"；验收时断言
`api._scan_tasks is lib._scan_tasks` → **True**（不是拷贝）。

**推广**：**抽离共享状态时，"同一对象"必须显式断言，"同名"不够。**
判据：`is` 断言比 `==` 断言强，因为它能抓住"拷贝了一份看起来一样的"。

---

### P-28 · 同名函数 ≠ 副本

**现象**：计划说"4 组重名函数族可归一"。逐个核对后发现：

| 函数 | 实际情况 |
|---|---|
| `_safe_div` ×3 | **完全等价** ⇒ 可归一 |
| `_to_float` ×6 | **语义不同**：字符串 "0" 在 data_eastmoney_fund / data_fund_flow 返回 **None**，在 data_layer / data_sina_fin 返回 **0.0**；千分位逗号只有部分实现剥离；亿 / 万只有 data_layer 剥离 |
| `get_kline` ×4 | **签名都不同**（data_tdx 无 as_of_date；data_westock 参数顺序不同） |
| `clear_cache` ×3 / `get_realtime_quotes` ×3 | 各清各的 / 实现各异 |

**根因**：同名是**历史巧合**，不是"复制粘贴"的证据。

**修复**：**参数化公共内核 + 各源显式声明策略**
（`lib/data/data_utils.py` 的 to_float(v, strip_grouping, strip_cn_units, zero_is_none,
zero_string_is_none, dash_is_none, prefer_raw_float) + 唯一的 `safe_div`），
重复的**转换逻辑**只剩一份，各源**差异被显式化**。

**推广**：**"同名"不等于"副本"，"同名不同语义"直接合并 = 静默改变数据语义。**
本例合并的后果是**改变财务数据语义** ⇒ 影响评分与回测且**无报错**。
判据：合并前先分类为「真副本」与「同名不同语义」；后者只能"参数化公共内核 + 各源显式声明策略"。

---

### P-29 · 特征测试要在合并前抓出等价性破坏

**现象**：特征测试（内嵌搬迁前实现作参考，对一批边界值逐值比对）当场抓出 **3 处不等价**：

1. 生成 wrapper 的脚本把 call_expr 当成完整调用 ⇒ 产出 `to_float(to_float(v, ...))` **双重包裹**，
   第一个位置参数变成函数对象本身；
2. `prefer_raw_float` 缺失 ⇒ `float(True)` 应为 1.0 却成 None（丢了 bool 语义）；
3. `zero_string_is_none` 与 `zero_is_none` 混用 ⇒ 字符串 "0" 的归属搞反。

**修复**：全部修正后再合并。

**推广**：**搬 / 改实现前先写特征测试，别靠人眼核对。**
等价性破坏在人眼里几乎不可见（尤其是"参数顺序反了"这类）。
判据：特征测试要**逐值比对**，不是"抽查几个看起来对的"。

---

### P-30 · 测试里复制实现会与真身漂移

**现象**：特征测试第一版里留了本地副本，参数更新后副本没同步 ⇒ **假失败**。

**修复**：测试应直接引用**模块真身**，不复制。

**推广**：**测试里复制被测实现，等于给自己埋了一个"第二个真相来源"。**
判据：测试引用模块真身；需要"搬迁前实现"时，把它作为**参考实现内嵌进测试**并明确标注是快照。

---

### P-31 · 计划里的判断要当场复核

**现象**：计划写"补齐熔断覆盖"，实测**网络源已全覆盖**
（baostock / tdx / adata / westock / tushare / akshare 均走 `breaker.call()`）；
未走 breaker 的 `tdx_local*` 是**本地文件读**，不走网络、不需要熔断。

**修复**：**不为凑指标加无意义代码**，当场判定为伪需求。

**推广**：**计划里的判断是当时的快照，不是现状 —— 动手前先 grep 现状 + 读实现。**
把"清单里写的缺口"与"代码里真缺的"分开再排期。
配套：跨批次、跨会话后必须重新核对；"已做但有残留缺陷"往往才是更好的抓手。

---

### P-32 · 文件名与目录名冲突

**现象**：计划里的 `api/core/` 目录不可用 —— Python 里 `api.py` 与 `api/` 同名不可共存。

**修复**：改为 `lib/infra/api_core.py`（符合项目 lib/ 惯例）。

**推广**：**新建模块前先查有没有同名文件 / 目录。**

---

### P-33 · 模块 import 期副作用

**现象**：`api.py:239` 模块级 `threading.Thread(target=_prefetch_hs300).start()` 会在 **import 时**
起 daemon 线程打 akshare 网络。不阻塞导入（实测 2.48s），但会让 CI 门禁产生外部网络副作用。

**修复**：加 `SKIP_HS300_PREFETCH` 开关，**默认行为完全不变**（不设 env 仍预取，已实测），
契约工具与 CI 走 `=1`。实测导入前后 `git status --porcelain` 新增变更 = **0**
（写入 `cache/`，已 gitignore）。

**推广**：**模块 import 期副作用会让工具 / CI 产生外部网络调用。**
解法是加**默认关闭**的 env 开关（运行时行为零变化），**而不是**在工具里 monkeypatch。

---

## 五、数据语义与前视偏差

### P-34 · 查前视偏差不能只看签名与文档

**现象**：`data_adata.get_kline(..., as_of_date=None)` 签名里有该参数、docstring 还写着"替换 datetime.now()"，
但实现里仍是 `end = datetime.now()` ⇒ **看起来已修、实际仍前视**。

**根因**：参数"收了"却没用，比压根没有这个参数**更危险** —— 后人看签名就以为已修。

**修复**：抽出可测纯函数 `_resolve_end_date` 并直接测它。

**推广**：**查前视偏差必须看实际赋值处，不能只看签名、类型标注与 docstring。**
判据：对每个"时间参数"，grep 它最终**流到哪个值**上。

---

### P-35 · 参数透传不完整

**现象**：上一轮只修了 tdx 三个源；本轮核查发现 `data_source_registry` 里
**baostock / adata / westock 三个源根本没透传 as_of_date** ⇒ 回测走这三源时仍取到 T 日数据。

**修复**：全部补齐；registry 层加 4 源参数化验证（baostock / adata / westock / tdx）。

**推广**：**加参数容易，透传参数难。**
判据：凡是"入口收了参数"，必须逐层追到**最终的数据获取点**，
并用**参数化测试逐源断言**（而不是抽查一个源）。

---

### P-36 · 少取也是前视偏差

见 P-14。此处补充判据：前视偏差的三种形态 —— **多取**（取到未来）、**少取**（窗口不含目标日）、
**错取**（参数收了没用）。三者都要查。

---

### P-37 · 合并同类实现会静默改变财务语义

见 P-28。补充判据：**金融数据语义的"看似等价"要按"下游影响"评估** ——
字符串 "0" → None vs 0.0 会改变"无数据"与"值为零"的区分 ⇒ 直接影响评分与回测。
**推广：数据层合并前先问"这两个实现对'空值'的判定一致吗"。**

---

### P-38 · PEP 562 懒加载使属性级 monkeypatch 失效

**现象**：`adata.stock.market` 用懒加载导出 get_market，`monkeypatch.setattr` 的 hasattr 探测会误判为缺失，
设 `raising=False` 也会真调网络。

**修复**：把日期计算抽成**纯函数**并直接测它，既避开包的内部结构，又保持测试**零网络**。

**推广**：**测第三方包时，优先"抽自己的纯函数"而不是"patch 别人的惰性导出"。**
判据：patch 能不能用，先看它是不是惰性属性 —— 惰性属性无法用普通属性 patch 可靠拦截。

---

## 六、CI 与额度治理

### P-39 · job 级 if 不能引用 matrix.*

**现象**：想把"只有某个 Python 版本才跑"写成 job 级条件：

```yaml
jobs:
  test:
    if: matrix.python-version == '3.12' || github.event_name == 'workflow_dispatch'
```

结果 **workflow 直接失败、job 列表为空**，控制台只说
"This run likely failed because of a workflow file issue."（**不指出具体行**，极难定位）。

**根因**：**job 级 if 在 matrix 展开之前求值** ⇒ 此刻 `matrix.*` 上下文不存在；
**step 级 if 才可用 `matrix.*`**。

**修复**：把过滤下移到 **matrix 层**：

```yaml
python-version: `${{ fromJSON(github.event_name == 'workflow_dispatch' && '["3.10","3.11","3.12"]' || '["3.12"]') }}`
```

**推广**：**job 级 if 与 matrix 的先后顺序，是 workflow 语义里的隐形陷阱。**
本地 `yaml.safe_load` 能挡 YAML 语法错，**挡不住表达式语义错** —— 那类只能靠 CI 的 job 列表来验。

---

### P-40 · Actions 额度算术

**现象**：GitHub Actions 免费额度按倍率计（**Linux 1x / Windows 2x / macOS 10x**）。

| 矩阵 | job 数 | 每轮消耗（折 Linux-分钟） |
|---|---:|---:|
| 3 OS × 3 Python | 9 | ≈ **117** |
| 3 OS × 1 Python（仅 3.12） | 3 | ≈ **39** |

**修复**：push/PR 只跑 3.12（3 job）；需要 3.10 / 3.11 兼容性验证时手动 `workflow_dispatch` 跑完整 9 job。
**已知取舍**：push/PR 不再自动覆盖 3.10 / 3.11。

**推广**：**PR 数与 job 数是乘数关系。**
省额度要先看矩阵维度（OS × Python），再看每个 job 的耗时。
配套：额度耗尽时把触发器临时改为仅 `workflow_dispatch`，
避免每次 push 产生一个跑不起来的红色 run（不耗分钟但刺眼）。

---

### P-41 · workflow 文件级错误不消耗 job 分钟

**推广**：**表达式 / 语法写错可以放心试错** ——
push 后看 **job 列表是否非空**：非空 = 解析成功；为空 = 文件级错误（不扣额度）；
只有真正跑起来才扣分钟。

---

### P-42 · 门禁结论取**步骤级**

**现象**：某轮 CI **9 个 job 全部 failure**，但失败点分两类：
9/9 失败在 `Run tests`（既有红，与本次改动无关）；仅 Windows 3.12 额外失败在契约步骤。

**修复**：把契约步骤放在 `Run tests` **之前** —— 让门禁能独立跑完并独立出结论，不被既有红淹没。
判定门禁时用：

```powershell
gh run view <id> --json jobs --jq '.jobs[] | "\(.name) -> \(.status)/\(.conclusion // "-")"'
```

**推广**：**workflow 的整体 conclusion 不等于单个门禁的结论。**
判据：验收门禁一律看**步骤级**；为此要刻意把门禁步骤排在耗时测试之前。

---

### P-43 · commit 失败后 push 仍会执行

**现象**：`git commit` 因 gitleaks 钩子拦截而失败，脚本继续执行了 `git push`，
把**旧 commit** 推上远端并触发了一次无意义的 CI（浪费一整轮 9-job）。

**根因**：PowerShell 的 `ErrorActionPreference='Stop'` **对 native command（git）不生效**。

**修复**：native 命令后必须显式检查 `LASTEXITCODE` 并中止；
或设 `$PSNativeCommandUseErrorActionPreference = $true`；**且 commit 失败时应立即停止，不要 push**。

**推广**：**在 PowerShell 里，"设了 Stop" 不等于"命令失败会停"。**
凡是"改完就提交并推送"的脚本，**每条 native 命令后都要检查退出码**。

---

### P-44 · 内容哈希会触发 secret 扫描

**现象**：契约快照里的 `"api_py_sha256": "<64 位 hex>"` 被 gitleaks 默认 `generic-api-key` 规则判为密钥（熵 3.89）。

**修复**：用**精准 regex allowlist**（``^[a-f0-9]{64}$``）而非放行整个目录 ——
意图明确、可审计，也不放过该目录未来出现的真 secret。

**推广**：**把内容哈希写进版本化文件会触发 secret 扫描器。**
修法要"窄"：精确正则优于目录级放行。

---

## 七、工具链与编码纪律

### P-45 · 编辑前必须先读原文

**现象**：用字符串替换编辑文件时多次因"凭记忆写 old_string"而匹配失败。

**修复**：**先读原文，再写 old_string。**

**推广**：**凭记忆构造的匹配串是不可靠的**，尤其在文件被前一轮改过之后。

---

### P-46 · 模板里的双花括号与换行需转义

**现象**：
- 在 JS 模板字符串里写 Actions 表达式（美元符号 + 双花括号）时，未转义会被当成本地模板插值；
- 在 Python 代码字符串里写换行转义时，未转义会产出真实换行而不是字面量，**多次产出语法错**。

**修复**：JS 模板里把美元符号写成反斜杠 + 美元；Python 源码里换行要写成双反斜杠。

**推广**：**跨三层转义（shell → 模板 → 目标语言）时，每一层都要单独确认。**

---

### P-47 · 生成代码的脚本必须先在小样本上验证

**现象**：批量生成 wrapper 的脚本把 call_expr 当成完整调用 ⇒ 产出
`to_float(to_float(v, ...))` 双重包裹，第一个位置参数变成函数对象本身。

**修复**：生成脚本先在小样本上跑，把产物打印出来人眼过一遍，再批量执行。

**推广**：**批量生成代码时，"脚本跑通了"不等于"产物对"。**
判据：生成前先在一两个样本上验证产物，再全量跑。

---

### P-48 · git add 用显式 pathspec

**现象**：`git add -A` 会把 `backups/`、`skills/` 等无关目录一并带入。

**修复**：**始终用显式 pathspec**（`git add <文件1> <文件2>`）。

**推广**：**凡工作区里有与本任务无关的产物，提交就必须用显式 pathspec。**

---

## 八、收官核验与实验纪律

本章的教训来自 Phase 3 收官核验：判据要**机器化取证**，实验要**不污染工作树**。

### P-49 · 同口径对照实验用 `git checkout <commit> -- <path>` 还原，会**污染工作树**

**现象**：为做覆盖率同口径对照，用
`git checkout 853da19 -- backtest/ lib/ api.py run.py scripts/` 切到旧版本，测完再用
`git checkout HEAD -- ...` 还原。**结果**：
① `backtest/benchmark.py` 与 `backtest/monte_carlo.py` 被还原到了**旧位置**
　（git mv 后的 legacy 副本还在 ⇒ 同一文件两份并存）；
② `scripts/coverage_baseline.py` 里新加的告警代码被**静默还原丢失**
　（`git status` 一度完全看不出异常 —— 还原后文件与 HEAD 一致）。

**根因**：`git checkout <commit> -- <path>` 是**覆盖工作树**，不是切分支；
还原时它按 **HEAD 的内容**覆盖，而 HEAD 与「实验前的真实状态」**不是同一个东西**
（我在 HEAD 之后还有未提交改动）⇒ 该命令会把实验期间的所有未提交改动一并覆盖掉。

**修复**：
① 立刻用 `git diff --stat` + 逐文件核对内容找回丢失的改动
　（本例靠 `git diff` 发现 `coverage_baseline.py` 的改动消失）；
② 残留副本用 `filecmp` 确认内容相同后再 `git rm -f` 清除。

**推广**：**同口径对照实验的还原方式有陷阱**：
- 用 `git stash`（含 `-u`）而不是 `git checkout <commit> -- <path>`
　⇒ stash 记的是「实验前的真实状态」；
- 若必须用 checkout，**实验前先 `git status` 存档**，实验后逐项核对；
- **判据：还原后必须 `git status` 复核**，不能假设「还原 = 回到原样」。

---

### P-50 · 覆盖率下降可能是**修复的正面结果**

**现象**：修掉 `lib/strategy/ab_testing.py` 的「静默降级到 mock」缺陷后，
该模块覆盖率 **34.97% → 31.45%（−3.52pp）**，会被覆盖率门禁判红（阈值 −2.0pp）。

**根因**：那些行的覆盖**来自 mock 路径** —— 真实引擎可用后 `except ImportError` 分支不再触发，
mock 的代码行自然失去覆盖。而 `tests/` 里 `ab_testing` **命中 0**（无任何测试）
⇒ 它从来没有被有意测试过。

**修复**：**补测试**（`tests/test_ab_testing.py`，5 例）⇒ 覆盖率 **34.97% → 83.65%（+48.68pp）**。
补测试时还当场抓到另一个真实缺陷：适配器读 `raw['trade_count']`，但引擎返回的是
`metrics.total_trades`（无 `trade_count` 键）⇒ **A/B 测试的「交易次数」恒为 0**。

**推广**：**覆盖率下降先问「是删了代码，还是不再走旧路径了」** ——
若是「不再走旧路径」（修好了缺陷），正确处置是**补测试**而不是用 `--allow-modules` 豁免。
豁免会把真实盲区永久留在基线里。

---

### P-51 · 门禁「静默跳过基线缺失的模块」是**看不见的盲区**

**现象**：覆盖率门禁里 `if not b: continue` ⇒ 基线中不存在的模块被静默跳过，
它们的覆盖率回归**永远不会判红**。发现 `backtest/` 从未进入基线
（CI 的 `--cov` 列表无 `backtest`）时，盲区已经形成。

**修复**：① CI 的 `--cov` 列表加入 `--cov=backtest`；
② 门禁增加 `[WARN]` **显式列出无基线的模块**（不改判定逻辑，只消除静默）。

**推广**：**任何「跳过」分支都要问「它跳过的东西会不会永远不被检查」。**
门禁里静默 skip 的项 = 永久盲区 ⇒ 必须让它**可见**。

---

### P-52 · 基线 pin 不完整 ⇒ 门禁**随日历漂移**（改动前就判红）

**现象**：2026-11-10 进入 Phase 4 取基线，工作树干净、代码与 Phase 3 收官完全一致，
L3 却报 `capital_flow` 4.0→5.0 / `score` 3.0→3.35，L4 报 `equity_first[0]`
`2025-12-22`→`2025-12-25`。基线采集于 **11-07**，正好差 **3 天**。

**根因**：`pinned_environment()` 的文档说 pin 住 as_of_date 与外部数据，
代码**只 pin 了数据源优先级 + 阻网**：
① `get_stock_score → full_score → _compute_base_scores` 的
`get_stock_fund_flow` / `get_news_signals` / `get_sector_score_for_stock` 未注入，
读到本地随时间更新的数据（+1.0 × 0.35 权重 = +0.35，精确解释总分差）；
② `backtest/engine.py:642` 在 `start_date` 缺省时取 `datetime.now()-timedelta(days=days)`，
并把它写进 `equity_curve[0]`。

**修复**：① 补齐三个入口注入（模块级 from-import patch **使用方**，延迟 import patch **定义方**）；
② 基线脚本用 `BASELINE_ANCHOR_DATE` 推导固定 `start_date` 并显式传参；
③ `tests/test_baseline_pinning.py` 6 例 AST + 扰动自测，把「不许出现 now()/today()」变成自动红线。

**推广**：**基线文档说「pin 住了什么」不算数，要逐个数调用点**。
凡输出里出现日期字符串、或链路有未注入的取数函数，基线就是随日历漂移的。
另：**判红时不要改基线数字、更不要挑一组「刚好能对上旧数字」的注入值**——
那是为迎合脆弱判据补数据；正确顺序是「修 pin 机制 → 重建无效基线 → 留 ADR」。

---

### P-53 · 计划里的「N 套实现 / 重复实现」是**假设**，动手前必须复核

**现象**：RF-01 §1.2 P1 写「评分体系 8 套并行」并据此定 Phase 4 退出判据
「8 套全部收敛为适配器」。实测 `lib/judge_strategies.py` **全仓 0 定义、0 引用**
（AST 类定义扫描 + `find` 双证），实际只有 7 套；且 7 套语义互不相同
（四维加权 / 打分卡 / 评委投票 / 风格 Agent / 投票决策 / 机会分 / 策略层 quick·full），
**不存在可合并的重复实现**。

**修复**：按「7 个模块 / 9 个入口」执行；归一对象由「合并算法」改为
「结果契约 + 分发内核 + 权重注册 + 真源声明」（`docs/adr/0003-*.md`）。

**推广**：这是 **ADR-0002（`check_entry` 三份不同策略）的复发**。
凡计划写「N 份重复实现，合并掉」⇒ 先做 AST 定义扫描 + 引用计数 + 返回值形状比对，
再决定合并还是归因登记。合并语义不同的实现会**静默改口径**，违反 `RULES.md §2.3 B1/B2`。

---

### P-54 · 对拍基线「9/9 全绿」但**毫无鉴别力**

**现象**：Phase 4 首版评分对拍 9 条链路全部 PASS、0 mismatches，看似安全网已就位。
做 `distinct` 检查才发现 `judge_panel` 60 个样本输出**只有 1 种**
（`evaluate_all()` 自 P0#13 起不再产生投票，恒返回 `HOLD/0`），`scorecard` 只有 2 种
（L1–L4 被 pin 成常数）。这种基线只证明「没崩」，锁不住任何口径。

**修复**：① `judge_panel` 改走 `evaluate_with_ml()`（生产主路径，产生 3 张 ML 票）；
② `market_brief.l1` / `industry_ret` 由样本确定性派生而非写死；
③ 采集后**强制检查每条链路的 `distinct` 与样本数**，8/9 链路达到 `distinct == sample_count`。

**推广**：**「全绿」不等于「有效」**。这是 P-10「加了门禁但从没见它红过」的翻版：
判据必须自证「输入变化时输出会变」，否则它只是个装饰。
`strategies_quick` 只有 4 种属离散加分函数的固有行为，需单独归因，不能一律判死。

---

### P-55 · patch 类上的 `staticmethod` 时，还原要用 `__dict__` 取描述符

**现象**：评分基线在 pin 上下文里把 `MarketFilter.layer1_market_trend` 等
5 个 `staticmethod` 替换成固定 lambda，退出时用 `getattr(Class, name)` 保存的原值还原。
`getattr` 返回的是**已解包的函数**，`setattr(Class, name, fn)` 会把它变成**实例方法**
⇒ 还原后调用多出一个 `self` 参数，污染同进程后续调用方。

**修复**：`orig = Class.__dict__.get(name)` 保存原始 `staticmethod` 描述符，
还原时 `setattr(Class, name, orig)`；没有描述符的对象（如普通方法）才用 `getattr`。

**推广**：**patch 的还原必须保持对象的描述符类型**。函数 / staticmethod / classmethod /
property 在类字典里是不同对象，`getattr` 会把它们统统抹平成可调用对象。
与 P-18（patch 目标用使用方）互补：一个管「改哪里」，一个管「怎么还原」。

---

### P-56 · 开关前后「0 差异」也可能是**两侧都没切**（空判据的 A/B 版）

**现象**：阶段 B 用「`FEATURE_UNIFIED_SCORING` off vs on，整份 `get_stock_score`
响应逐字段 diff = 0」证明灰度安全。但 diff 为 0 有**两种**可能：
① 新旧路径数值确实一致（想要的结论）；② on 侧根本没走到内核，两次都跑旧实现
（例如环境变量名写错、消费者名不匹配、网关没被调用）。只看 diff 无法区分。

**修复**：`scripts/scoring_switch_diff.py` 在 on 侧 patch
`scoring_adapters.build_kernel` 计数，断言 `off=0 / on>0`；
再用 `tests/features/test_scoring_switch.py` 的 AST 守卫固定
「消费者不许自读 flag、必须调 `score_4d("strategies", ...)`」。
实测 `strategies` 与 `all` 两种取值均 off=0 / on=3。

**推广**：**任何 A/B 对拍，先证明 B 侧真的走了 B**（P-54「全绿不等于有效」的 A/B 版）。
另外：**环境变量也是外部输入** —— `scoring_baseline.py` 的 pin 环境必须固定
`FEATURE_UNIFIED_SCORING=off` 并写进 `constraints`，否则「今天谁开了灰度」会改读数（P-52 的推广）。
但 `dual_run_diff.pinned_environment()` **不能**固定它：开关取证脚本复用该上下文做同进程切换，
固定即失效 ⇒ pin 工具链的接口约束要写进 ADR，不能靠默契。

---

### P-57 · 切了第二个消费者，取证脚本却只跑主路径

**现象**：阶段 B 第 1 批的 L6 取证用 `dual_run_diff` 注入的 `rt_row`（非空），
只覆盖 `score_with_realtime → strategies` 一条路径。第 2 批切了
`score_without_realtime → pipeline` 后，若沿用原脚本，新消费者**根本不会被跑到**，
却仍会打印 `0 field(s) differ` ⇒ 绿灯是假的。

**修复**：`scoring_switch_diff.py` 扩展为 `realtime` / `no_realtime` 两条分支；
后者在 pin 上下文内把 `fetch_stock_data` 的返回改成 `rt_row=None` + dict 形态
`fin_data`，真正进入无实时行情路径。每个分支**单独**统计内核调用数并单独比对；
flag 未命中的分支只报 `[INFO]`，其 diff 不作为证据。

**推广**：**取证覆盖面必须跟着消费者数量一起增长**。每新增一个分支就问
「现有脚本有没有可能一次都没碰到它」，并让「命中数 > 0」成为硬断言。
另外：把旧函数**原样抽成纯函数**时，`fin_data.get(...)` / `getattr(fin_data, "pe", ...)`
这类看起来可疑的分支一律不许顺手修 —— 它们是既有口径，改一处就变评分数值；
先用 `git show HEAD:<path>` 动态加载旧函数做数值对拍，再把等价式固化进测试。

---

### P-58 · 计划里的「消费者」可能是**纯透传**，硬切就是加补丁

**现象**：计划 §三 Phase 4 把 `/api/score/{code}` 列为待切换消费者第 3 项。
逐层核对发现它是三层纯透传：
`api.py:414 stock_score` → `scanner.py:61 ScanOrchestrator.get_stock_score`
→ `pipeline.py:522 get_stock_score`，
端点函数体只有参数校验 + 一次转调 + 异常映射，**没有任何评分计算**。
下游两条评分路径在前两批已接入网关 ⇒ 端点本来就会受益。

**修复**：① 端点**零代码改动**，登记为 `PASSTHROUGH_CONSUMERS = ("api_score",)`；
② 把「切端点」重新定义为可验证动作 —— L6 新增 `endpoint` 分支，用
`asyncio.run(api.stock_score(code, _=None))` 真正调用端点函数，off/on 比对整份响应；
③ 用 AST 守卫钉住「它就是透传」，防止以后有人往里塞 flag 分支。
**明确否决** `if unified_scoring_enabled("api_score")`：判定散落、无对应适配器、数值零收益。

**推广**：**清单里的名词是假设，动手前先数调用点**（P-53 的第三次复发）。
「切了几个消费者」不能只看清单打勾数，要看「这个调用点有没有自己的评分计算」。
没有切点时的正确交付是**取证 + 登记 + 结构冻结证明**，不是造一个分支出来。
顺带把消费者级自证制度化：L6 逐个校验 `GRAY_CONSUMERS`，新增名字没被覆盖会立刻暴露。

---

### P-59 · 「没有切点」也分三种，别全塞进一个「透传」抽屉

**现象**：收官阶段 B 时，把剩余两个端点按 P-58 的经验一律当纯透传处理。
AST 扫描调用名集合后发现三者性质完全不同：

| 端点 | 调用 | 真相 |
|---|---|---|
| `/api/score/{code}` | `get_stock_score` | **透传**：下游评分路径已接网关 |
| `/api/dashboard` | `_cache_get` / `_cache_set` / `get_market_sentiment` | **聚合**：评分仅经 `run_all_strategies → strategies` 间接受益 |
| `/api/strategy/recommend` | `FactorOptimizer` / `evaluate_weights` | **域外**：因子权重优化，根本不消费评分结果 |

**修复**：网关拆成互斥四类登记（`GRAY` / `PASSTHROUGH` / `INDIRECT` / `OUT_OF_SCOPE`），
测试断言四类无交集且覆盖三个端点；`--summary` 全部列出。

**推广**：① **给域外端点做 off/on diff，结果是恒 0 的空判据**（它根本不调网关，
内核调用也恒 0）⇒ 改用 AST 守卫钉住「它不消费评分结果」；
② **给缓存聚合端点做端点实跑，缓存命中会让 off/on 返回同一对象**，
同样是无鉴别力绿灯，且自动触发 scan 带超时/网络依赖会让门禁 flaky
⇒ 改判据为「分类登记 + AST 边界守卫 + L2 契约快照」，并留 ADR（本项目第 4 次判据修正）。
**判据必须能区分「真的没变」和「压根没跑到」。**

---

### P-60 · 收官验收被**既有间歇性用例**假红打断，别急着改断言

**现象**：阶段 B 收官时全量 L1 报
`test_signal_tracker.py::TestBorrow2AtomicWrite::test_concurrent_save_history_no_data_loss`
失败（`OSError: 无法获取信号历史文件锁`）。该用例是 `multiprocessing spawn` 真并发
+ portalocker 文件锁，README §五 早就登记为「三平台既有失败」。

**诊断**（三步取证，顺序不能反）：
1. **改动范围**：`git diff --name-only HEAD` ⇒ 本批 10 个文件全是文档 / 网关常量 /
   L6 脚本 / 测试，**没有** `lib/scoring/signal_tracker.py` 或 `tests/test_signal_tracker.py`。
2. **单独复跑**：同一用例串行单跑 5 次 ⇒ **4 通过 / 1 失败**，确认间歇性而非回归。
   并行跑其它重负载取证任务时失败率明显升高（首轮曾连续 3 次失败）。
3. **干净全量复跑**：不并行任何其它任务 ⇒ **1253 passed / 12 skipped，0 failed**。

**修复**：**没有修它**。根因在 portalocker 跨平台锁超时/重试策略与并发测试设计，
属独立批次；本批不动 `signal_tracker`，不加 `skip`，不改断言，不放宽门禁。
如实登记为收官「已知遗留」并给出独立批次建议。

**推广**：**判红先问「我这次改的东西能不能碰到它」**，用 `git diff` 而不是直觉作答。
再区分「稳定失败」与「间歇性」：连续单跑 ≥5 次 + 一次无并行干扰的全量复跑。
切忌为了让收官好看而加 `skipif` 或调容忍度 —— 那是把真实盲区压下去（P-51 同构）。

---

### P-61 · 拆大文件前不先固化「拆之前是什么样」，拆完就无法归因

**现象**：Phase 5 要把 3,000+ 行的 `api.py` 按 17 域拆成 router。
计划里虽有 17 域归属表，但它是**文档表格**（人工维护、无机器判据）；
退出判据还写「前端 18 条路径实跑」，实测 `static/**` 字面量提取得到 **36 条**
（含 `${code}` 模板参数与 `/api/score/` 这类拼接前缀），口径差异无法复现
（Phase 0 采集脚本未入库）。这是清单数字第 5 次与事实不符。

**修复**：① 拆分前新增 `scripts/route_ownership_check.py`，从**运行时 `app.routes`**
生成 17 域 × 68 端点的 `route-ownership.json` + 前端 `frontend-api-paths.json`；
② `--check` 五重校验（成员+数量、归属完整性、鉴权/async 不得减少、基线 diff、契约 diff）；
③ 计划表降级为规划基线，运行时快照才是权威；
④ 「18 条」按实测修正为 36 条并留 ADR-0004，保留 `plan_stated_count: 18` 作对照。

**推广**：**重构大文件前，先把「现状」变成机器可判据，再动第一行代码**。
尤其鉴权依赖：拆成 router 时漏挂 `Security(verify_api_key)` 会静默暴露端点，
靠肉眼审 68 条端点不可靠。另外：口径数字（68 端点 / 65 路径 / 36 前端路径）
必须写清**计数单位**，否则「68 还是 65」会变成下一轮的无谓争论。

---


---

---

### P-66 · portalocker 的 acquire() 重试只覆盖「加锁」不覆盖「打开文件」，Windows 多进程抢开 .lock 会误判「拿不到锁」

**现象**：`lib/scoring/signal_tracker.py` 用 `portalocker.Lock(...).acquire()` 加锁，
`timeout=30` 以为能扛并发。但 `test_concurrent_save_history_no_data_loss`（2 进程 spawn 抢写）
间歇 `OSError: 无法获取信号历史文件锁`，根因是 `PermissionError: [Errno 13] Permission denied`
发生在 `portalocker/utils.py::_get_fh`——即**打开** `signal_history.lock` 那一步，而非加锁 syscall。
该异常逃出 `acquire()` 的（仅覆盖加锁的）重试循环，被我们的代码包成「拿不到锁」的 IOError。

**根因**：portalocker 3.2 的 `acquire()` 先把 `_get_fh()` 放在重试循环**外**，循环内只重试 `_get_lock`；
Windows 上多进程同时 `open()` 同一 `.lock` 文件会间歇抢不到（文件共享语义），抛 PermissionError 直接上抛。

**修复（门户层兜底，不 skip / 不降门禁 / 不改断言）**：新增 `_acquire_portalocker_with_retry()`，
把「打开」和「加锁」任一瞬时失败（PermissionError / `portalocker.exceptions.LockException`）
都在 30s 窗口内重试；内层 `acquire(timeout=0.1)` 把「持锁被占」也归入瞬时争用。
回归测试 `TestPortalockerOpenContentionRetry` 用 mock 验证「首次 PermissionError 重试后成功」
与「耗尽超时仍抛出」，使下批不可能再犯。

**推广**：**第三方库的「重试」覆盖到哪一步，必须读源码确认**。任何「超时=安全」的假设，
要先看它的重试循环到底包住了哪些步骤；漏掉的那一步（如打开文件）就是间歇失败的藏身处。

---

### P-67 · 「扫描 async 端点体内直接调用同步 IO」是空判据：搬进 helper 就消失，且绝对阈值会被实测推翻

**现象**：Phase 6 计划写退出判据「静态扫描 async 端点体内直接调用同步 IO，计数下降至 ≤ 1」。
装好第 8 把尺子一采基线：**68 端点 / 1052 调用 / 242 个阻塞调用 / 63 个端点含阻塞点**，
与「≤1」差两个数量级。更糟的是，第一版只扫端点函数体时，
`routers/common.py::_load_alert_rules` 里的 `open` / `json.load` 完全不在扫描结果里 ——
`/api/alerts/rules` 等 3 个端点看起来「干净」，实际每次请求都在事件循环里读文件。

**根因**：两个独立的判据缺陷叠加。
① **计数单位错**：把「调用次数」当绝对阈值，但 242 次里绝大多数是轻量缓存读
（`_cache_get` 25 次、`exists`/`stat`/`glob` 元数据查询、`json.load` 2 次），
要压到 ≤1 得把 63 个端点整体改成线程池调度，与「不改 `async def` 签名」直接冲突。
② **扫描边界错**：只看端点函数体，没有跟随调用链；把阻塞调用搬进同步 helper 即可绕过，
「修完」只是把符号藏起来了，事件循环照样被占住。

**修复（改判据，不改数据去迎合判据）**：
- 计数单位改为 `(endpoint, callee)`，判据改为**单调下降**（blocking 只准降 / offloaded 只准升）；
- 端点调用 `api.py` / `routers/*.py` 顶层同步函数时**递归展开**（depth ≤ 2 + 访问集防环），
  实测因此多捕获 95 次调用、覆盖 19 个端点（depth1=92 / depth2=3）；
- `other` 逐字段冻结：阻塞符号表**不假设完整**，表外新符号（如 `requests.get`）会触发漂移判红；
- 泛名 `get`/`load`/`run`/`save` 只按完整点号名分类，`dict.get` 不被误判为网络请求；
- 保留 `plan_stated_target: "blocking call count <= 1"` 与实测 242 并列，不悄悄对齐（ADR-0006）。

**推广**：**「扫描 X 体内有没有 Y」这类判据，必须先问「X 能不能把 Y 搬走」**。
能搬走就必须跟随调用链，否则修法会退化成藏符号。
另外，凡是**绝对计数阈值**，动手前先采一次基线：阈值来自计划估算而非实测时，
它不是验收线，而是一个待验证的假设；判据被推翻要按先例改判据 + 留 ADR + 补机器判据，
不能删条目、放宽阈值或挑一组刚好对得上的数据（G3 同构）。
本项目这已是第 6 次清单数字被实测推翻。

**证据**：`docs/refactor/contracts/blocking-io-baseline.json`；
`python scripts/blocking_io_check.py --summary`；`--selftest` 4 类回归各自命中指定门禁；
`docs/adr/0006-phase6-blocking-io-criteria.md`；`tests/test_blocking_io_check.py`（11 例）。

---

### P-68 · 把 `lineno` 写进基线，「其余链路逐字节一致」会退化成不可执行

**现象**：Phase 6 第 2 批把 `routers/common.py::_flush_metrics()` 的函数体收口成薄壳，
文件少了约 14 行。随后按 G4 重建 L8 基线时，
`--recapture --mode refactor --allow-endpoints "GET /api/scan,GET /api/scan/status"`
报 **16 个「UNEXPECTED drift in untouched endpoint」**，但这 16 个端点一行代码都没改：
`alerts` 系列、`/api/dashboard`、`/api/market`、`/api/risk`、`/api/score/{code}` …
而同一时刻 `--check` 只报了 2 个端点漂移。

**根因**：`calls` 记录里带 `lineno`，而 helper 展开是按**源码行号**记录调用位置。
`common.py` 里删掉一段代码，其下方所有 helper 的行号集体前移，
于是所有经 helper 展开的端点记录都「变了」。但 `--check` 只按 callee 计数比较
`other` 字典，不含行号 ⇒ 两个口径给出不同结论。
**行号是实现细节，不是行为契约**（P-05 dtype 同构）：把它纳入逐字节比较，
等于要求任何无关编辑都必须重建全部端点基线，
进而诱使执行者把这 16 个端点加进白名单 —— 那才是真正的补丁加补丁。

**修复**：`blocking_io_check.py` 新增 `_normalize_endpoint()`，机器比较时去掉 `lineno`
再逐字段比；`lineno` 仍保留在基线里供人排查「这个调用在哪」。
`recapture` 的 evidence 增加 `comparison` 字段写明口径。
结果：16 个未改动端点恢复为「逐字段一致」，只有真正改动的 2 个端点被允许变化。

**推广**：**设计基线字段时先问「这个字段会不会被无关编辑改变」**。
会变的字段（行号、时间戳、绝对路径、随机盐、dtype）只能进「人读」部分，
不能进「机器比较」部分；否则门禁越严越不可用，最后一定被绕过。
判据要能区分「记录变了」和「行为变了」。

---

### P-69 · 取证脚本导入业务模块时，模块级后台预取线程会污染 off/on 对照

**现象**：`scripts/offload_switch_diff.py` 首次实跑，stdout 里出现两次
`后台预获取沪深300数据... 沪深300数据缓存成功`，每次约 4.4 秒。
同一次 `--check` 里 off 与 on 两侧先后执行，后台线程可能在两次采集之间写入
`hs300_cache.parquet` / `_cache_set("hs300", ...)` ⇒ 两侧读到的缓存不一致，
「响应 diff = 0」即使通过也不能证明 offload 没改行为。

**根因**：`routers/common.py` 模块级有
`if not SKIP_HS300_PREFETCH: threading.Thread(target=_prefetch_hs300).start()`。
`from routers import backtest` 会连带导入 common ⇒ 触发网络预取。
`contract_snapshot.py::collect()` 早就在导入前设了 `SKIP_HS300_PREFETCH=1`，
新脚本没有沿用这个既有口径（P-17 / P-33 的同构复发）。

**修复**：在脚本模块顶部、任何 `routers` 导入之前强制
`os.environ["SKIP_HS300_PREFETCH"] = "1"`，与其他门禁脚本同一处理口径；
并用 `unittest.mock.patch` 注入确定性回测结果，整条取证链路零触网。

**推广**：**凡「同一进程内做 A/B 对照」的工具，第一件事是清点模块导入期副作用**
（预取线程、定时器、单例初始化、缓存预热）。默认禁网 fixture 只能挡 socket，
挡不住「导入时起的后台线程」；这类线程必须在导入前用环境变量关掉，
而不是事后 mock 它的结果。

---

### P-70 · 异步压测里「协程内计时」会得到完全相反的性能结论

**现象**：`scripts/concurrency_probe.py` 首次实跑，20 并发 × 3 轮，
off P95 ≈ **20.7ms**、on P95 ≈ **209.4ms**，ratio ≈ **10.1** ——
看起来 offload 让端点慢了 10 倍，`--check` 三条 P95 全判红。

**根因**：计时起点放在每个协程内部（`t0` 在 `one()` 里设），
而**调度方式本身改变了 t0 何时被设置**：
- off 侧 `run_blocking` 是同步直调，`time.sleep(20ms)` 占住事件循环，
  后面的协程要等前面跑完才**首次被调度** ⇒ t0 设得晚，等待时间完全没被计入，
  单请求耗时虚低到只有自身阻塞时长；
- on 侧让出事件循环，20 个协程早早全部设好 t0 再去等
  `_backtest_semaphore(2)` ⇒ 排队时间全被计入，虚高到整批墙钟时间。

两边测的**根本不是同一个量**。真实总耗时其实是 off ≈ 400ms / on ≈ 200ms，
offload 快一倍。

**修复**：改成**从批次发起算起的完成延迟**（客户端视角）——
一轮只设一个 `batch_start`，所有请求用 `time.perf_counter() - batch_start`。
复跑 off P95 389–404ms、on P95 208–220ms、ratio≈0.54，3/3 改善。
**阈值一点没放宽**（仍是 `on <= off × 1.10`），只改测量口径。

**推广**：**A/B 性能测试必须先证明两侧计的是同一个量**。
判红时先问「是不是尺子错了」，再看是不是被测对象慢了；
尤其当「优化后反而慢一个数量级」时，十有八九是计时起点被调度方式带偏了。
另外，压测要同时自证 on 侧真的换了线程（记录工作线程 ident），
否则「两边都没切」也能跑出漂亮的对比数字（P-54 / P-56 同构）。

---

### P-71 · 「逐条留痕」若靠人工分类，遗留会被改名成已完成

**现象**：Tier B 登记要求对 228 个阻塞调用逐条写保留理由。
第一次生成有 **7 个 unclassified**（`csv.writer`、`pdf_generator.generate`、
`arr.sum`、`t.start`、`snapshot_manager.restore/list` 等），
且 `generate` / `writer` / `start` / `list` 这类**泛名**只看尾名会与完全不同的操作混淆。
更关键的风险：35 个端点含 `_get_orchestrator` / `compute_all_signals` /
`_batch_get_stock_hist` 等重计算符号 —— 如果允许手工把它们填成「轻量 IO 已留痕」，
清单会显示「全部登记完毕」，而实际上这些端点根本没 offload。

**修复**：分类由**符号表机械判定**（`_tier_b_group`），泛名按完整点号名覆盖
（P-28 同构）；`--check` 校验每条 `group` 必须等于机械判定结果，
篡改即判红；未分类符号 > 0 时 `--tier-b` 拒绝落盘（P-08 漏斗全覆盖）。
含重计算符号的端点**自动进 `tier_a_candidates` 并标 `status: not_offloaded`**，
作为 Phase 6 的显式遗留列出（35 个）。

**推广**：**凡「逐条登记理由」类台账，理由的类别必须机器可复算**，
否则它就是一张可以自由填写的表格。判据要能区分
「已评估并接受」和「没做但改了个名字」；
把遗留显式列出来，比让台账显示 100% 完成更有价值。

---

### P-72 · L8 `recapture` 误报「未改端点漂移」：端点顶层 `lineno` 漏剥（P-68 复发，根因修）

**现象**：Phase 6 第 4 批在 4 个 router 顶部各加一行
`from lib.infra.async_offload import run_blocking`，随后按 G4 重建 L8 基线，
`--recapture --mode offload` 报 **21 个「UNEXPECTED drift in untouched endpoint」**，
但这 21 个端点一行业务代码都没改。`--check` 只报了 5 个真正变化的端点。

**根因**：第 2 批 P-68 只修到「剥 `calls[].lineno`」，漏了端点**顶层** `lineno`。
在 router 顶部加 import 使下方所有 handler 行号整体下移，于是所有经 helper 展开、
其调用点记录挂在端点顶层 `lineno` 下的端点整条被判 drift。两个口径（recapture 逐字节 vs
`--check` 只比 callee 计数）给出不同结论 —— 与 P-68 同源，只是漏剥的位置不同。

**修复**：`_normalize_endpoint()` 在剥 `calls[].lineno` 的同时，**同步 `pop` 端点顶层 `lineno`**：
```python
out = copy.deepcopy(record)
out.pop("lineno", None)
for call in out.get("calls", []):
    call.pop("lineno", None)
return out
```
`lineno` 仍保留在基线 JSON 里供人排查「这个调用在哪」，但**不进机器比较口径**。

**推广**：**「在模块顶部加 import / 删一个函数体」这类无关编辑，会让所有下游行号漂移；
凡基线字段会随无关编辑变化，就都不能进机器比较**（P-68 同构）。
本次是 P-68 的复发：只补一处漏剥不够，要确认所有层级的 `lineno` 都被排除。
判据要能区分「记录变了」和「行为变了」；漏剥诱使执行者把无关端点塞进
`--allow-endpoints` 白名单 = 补丁加补丁，根因修法是改口径（ADR-0010 D3）。

---

### P-73 · L9 对拍 `patch("datetime.datetime.now", ...)` 对 C 扩展不可变类型直接崩

**现象**：Phase 6 第 4 批把 L9 扩展到 8 个迁移，首跑即
`TypeError: cannot set 'now' attribute of immutable type 'datetime.datetime'`
—— `datetime.datetime` 是 C 扩展不可变类型，不能给它的类属性 `.now` 赋值。
同时 `_old_try_body` 返回旧 try 体、**未剥前导 import**，而 `_new_fn_body` 剥了，
导致「旧 10 条 / 新 7 条」的假计数差，L9 误报 7 处 statement count 不一致。

**根因**：两处独立缺陷。
① 冻结 `datetime.now` 想用 `patch("datetime.datetime.now", return_value=...)`，但 `datetime` 类型的
   方法不是可写属性；且 router 经 `from .common import *` 持有自己的 `datetime` 名字绑定，
   即便 patch 模块级 `datetime` 也改不到各 router 命名空间。
② `_strip_leading_imports` 只套在 `_new_fn_body` 上，`_old_try_body` 没套 ⇒ 旧新不对称，
   语句数天然不等。

**修复**：① 改用 `Mock(wraps=datetime.datetime)` 保留真实方法、只钉 `.now` / `.utcnow`，
   并**逐 router 命名空间** patch 才能改到经 `from .common import *` 持有的绑定：
```python
from unittest.mock import Mock, patch
frozen_dt = Mock(wraps=_dt.datetime)
frozen_dt.now.return_value = FIXED_DT
frozen_dt.utcnow.return_value = FIXED_DT
for _ns in ("routers.factor", "routers.risk", "routers.export", "routers.common"):
    try:
        stack.enter_context(patch(_ns + ".datetime", frozen_dt))
    except AttributeError:
        pass
```
② `_old_try_body` 的返回也套上 `_strip_leading_imports`，与 `_new_fn_body` 对称归一。

**推广**：**冻结标准库不可变类型（C 扩展：`datetime.datetime` / `decimal.Decimal` 等），
不要 `patch` 其类属性，用 `Mock(wraps=真实类型)` + 只钉要控的方法**；且名字绑定可能
分散在多个命名空间（star-import / 局部 `import as`），要逐个命名空间 patch，不能只动定义方
（P-18 / P-19 同构）。另外：**AST 比对的两边必须套同一套归一化**，否则「旧 10 条 / 新 7 条」
这类对称缺陷会让判据假红（P-45 的「比对前先归一」纪律）。

### P-74 · 对拍 mock 返回 falsy `{}` 撞上 `is not None` 缓存判断，被测路径根本没执行

**现象**：Phase 6 第 5 批把 `/api/risk-exposure` 接入 L9 对拍，首跑即
`switch diff GET /api/risk-exposure: on-side did NOT use thread pool (count=0)`。
而响应 off/on diff 是 0——两项判据一红一绿，红的那项才是真的。

**根因**：`routers.risk._cache_get` 的既有 mock 是
`lambda key, **k: None if key == "risk" else {}`——为第 4 批 `/api/risk` 设计
（`_compute_risk` 里 `scan_cache = _cache_get(...) or {}`，`{}` 恰好表示「空缓存」）。
但 `/api/risk-exposure` 端点判的是 `if cached is not None: return cached`，
`{}` 不是 `None` ⇒ 缓存命中分支直接返回 ⇒ **`run_blocking` 一次都没被调用**。
off/on 响应当然恒等（都是同一个 `{}`），对拍成了空判据——和 P-56 同构，
但方向相反：P-56 是「两侧都没走到 ⇒ 0 差异」，这次是「根本没进被测路径 ⇒ 0 差异」。

**修复**：mock 按 key 精确放行：`None if key in ("risk", "risk_exposure") else {}`。
命中由 L10 的线程池自证抓出（`on-side pool_calls` 必须 == 1），响应 diff 单独存在时
**证明不了** anything——这正是第 2 批坚持「off=0 / on=1 + 工作线程 ident 不同」
三层自证的原因（T4）。

**推广**：**给带缓存的端点写对拍 mock，必须先读端点的缓存判断写法**
（`is not None` vs `or {}` vs `if cached:`），falsy 值只在「or {} / if cached」语义下等价于
未命中。同一命名空间的 mock 被多个端点共用时，新增消费者要重新核对每个 key 的
分支语义，不能假设旧 mock 的「空值」对新端点也是「未命中」（P-57 同源）。

**复发（2026-11-12，第 6 批）**：同一次修法里改了 `risk`/`risk_exposure` 两个 key，
紧接着给 `/api/exposures` 建 mock 时又对它的 key 默认 `{}`——同一文件、同一种
`is not None` 判断，L9 首跑即 `on-side did NOT use thread pool (count=0)`。
根因：修正落成了「key 清单」而不是「规则」。教训：**核对单位是 (文件, key, 判断写法)
三元组**，同文件新增 key 不继承上次的修正；mock 旁要留注释写明哪些 key 判
`is not None`、哪些判 `or {}`。

### P-75 · 结构守卫钉「名字出现」，坏 import 的名字也算数——坏端点长期全绿

**现象**：Phase 6 第 7 批迁移前排查 mock 目标时发现 3 个端点**真实运行时必然失败**：
`/api/notifications`、`/api/notifications/stats` 调用
`notification_logger.get_notifications(...)`，但该模块只有 `NotificationLogger.get_recent`、
没有模块级 `get_notifications` → AttributeError；`/api/strategy/recommend`
`from lib.scoring.factor_optimizer import FactorOptimizer`，但该模块只有
`GeneticOptimizer` / `run_optimization` → ImportError。三个端点全走 `except` 空兜底，
而相关守卫测试（如 `test_recommend_is_out_of_scoring_domain`）**长期全绿**。

**根因**：守卫用 AST 钉「端点体内出现 `FactorOptimizer` 名字」——名字出现在
坏 import 语句里也算数。**名字存在 ≠ 符号可解析**；AST 静态检查不执行 import，
解析失败只在运行时暴露，而失败被端点自己的 `except Exception` 空兜底吞掉，
响应「看起来正常」（空结构），没有任何红灯。

**修复**：Phase 6 不改行为（逐字搬迁原样保留 bug，mock `create=True` 注入符号
取证搬迁体逻辑）；bug 修复单独立项（TODO 登记，修复会改变响应内容，须走独立
fix 批次 + 契约快照对照）。守卫本身在第 7 批已随 offload 边界改写为双函数断言
（ADR-0012 D2 / ADR-0013 D4）。

**落地（RF-02 / ADR-0015，2026-11-13）**：D1/D2 已修复——notifications 改用
`notification_logger.get_notifications`（模块级新增）+ 检索 `title/body`；strategy/recommend
改用真实 `eval_current_weights` 替换幻影 `FactorOptimizer`。修复恰好改动了 L9 `check_migration`
冻结的 `_compute_*` 体 → 门禁判红；经用户确认**演化门禁（非削弱）**：新增 `MIGRATION_DIVERGENCES`
清单，对这 2 个迁移豁免逐字等价、仅校验接线完整性，行为正确性改由
`tests/features/test_rf02_bad_endpoints.py` 覆盖；原钉名字的守卫 `test_recommend_is_out_of_scoring_domain`
也改为断言 `eval_current_weights` 在、`FactorOptimizer` 不在（见 P-76）。全量 1301 passed / 0 failed。

**推广**：**AST 钉名字的结构守卫只能证明「写了这个名字」，证明不了「这个名字
可解析/可调用」**。给 import 的符号加守卫时，至少补一条 importable 冒烟断言
（`importlib.import_module` + `getattr`）；端点有自己的 `except Exception` 空兜底时，
「响应正常」对坏路径完全不敏感——这正是 P-54「对拍全绿但没锁住任何东西」的
静态版本。

---

### P-76 · 迁移冻结门禁 vs 有意行为修复：用「分歧清单」演化门禁，而非削弱

**现象**：L9 `check_migration()` 把 offload 后的 `_compute_*` 体 AST 逐字冻结为「offload 前端点体」，
作为迁移等价判据。RF-02 的 D1/D2 修复的恰好是这两个 `_compute_*` 体（notifications 检索
`message/details`→`title/body`；strategy/recommend 幻影 `FactorOptimizer`→真实 `eval_current_weights`）——
这必然改动冻结体，于是 `test_pre_migration_commit_is_pinned_and_readable` 判红：
`notifications: statement #4 changed (If vs If)`、`strategy_recommend: statement count 4 -> 2`。

**根因**：冻结体的设计意图是「阻止 offload 批次的逐字搬迁被偷偷改」，但它无法区分
「offload 批次的悄悄改动」与「后续独立 fix 批次的合法行为修复」。D1/D2 是 P-75 已登记、用户确认的
**有意分歧**（不是顺手优化），却被冻结门禁一视同仁判红。

**修复**：经用户确认**演化门禁、不削弱**——在 `scripts/offload_switch_diff.py` 新增
`MIGRATION_DIVERGENCES: dict[str, str]` 清单，登记这 2 个迁移及其成因；`check_migration()`
对清单内迁移跳过逐字 AST 等价，仅保留「接线完整性」校验（compute 函数仍存在、消费者登记双向对齐）。
门禁结论改为 `35 endpoints migration-AST-equivalent + 2 intentionally diverged`。行为正确性由
`tests/features/test_rf02_bad_endpoints.py`（8 例）兜底，而非塞回冻结体。

**推广**：**冻结门禁的判据要能区分「同一批次的悄悄改动」vs「后续批次的有意分歧」**。
当修复不得不触碰被冻结的契约时，正确收口是「把合法分歧显式登记进门禁（白名单 + 成因注释 +
替代校验）」，而不是 (a) 把修复退回去迎合冻结、(b) 整体放宽门禁、(c) 给门禁加特例分支绕过。
分歧清单必须带「为什么分歧」与「由谁兜底」，否则等于削弱门禁、埋下无人复核的盲区。

### P-77 · `list.copy()` 不是「数据副本」：`List[Dict]` 浅拷贝让修正函数就地篡改入参

**现象**：`lib/data/data_correction.py::correct_data` 用 `corrected = data.copy()` 造"副本"，
随后 `corrected[idx]["close"] = v` / `["volume"] = v` 写回。实测（2026-11-13，构造 `confirms=False`
走真实修正写回）：调用方传入的 `data[2].close` 从 300 被静默改成 100，`data[2] is out[2]` 为 **True**，
且 volume_spike 路径同样污染。更隐蔽的是：多个 anomaly 连续修正时，前一个已写回的值会成为后一个
「前 N 日均值」lookback 的基准，造成**依赖 anomaly 顺序的静默数值漂移**。
原 3 个测试全绿 —— 它们只断言「返回值等于期望」，从没断言「入参没变」，缺陷因此潜伏 5 个月。

**根因**：`list.copy()` 只复制容器、不复制元素；元素是 mutable dict，两个 list 共享同一批对象。
这不是"少写了一句 deepcopy"的疏忽，而是**契约缺口**：函数名叫 `correct_data`、返回 `corrected`，
"返回修正副本、不动原始数据"的语义只存在于人脑里，从未被机器判据钉住。
修正缓存场景下原始值必须保留（审计、重放、重复修正），污染入参等于把审计证据一起改掉。

**修复**：根因修，不在调用处加包装 ——
① `correct_data` 改 `corrected = [row.copy() for row in data]`（逐元素），docstring 显式写
「返回与入参完全独立的修正副本（不修改 data）」；
② 补 **4 例机器判据**（`tests/test_data_correction.py`）：2 个红测锁 `price_jump` / `volume_spike`
两条写回路径的「入参不变 + `corrected[i] is not data[i]`」，2 个绿灯守卫锁 `confirms`
（次日确认新价位）与 `is_limit`（涨跌停）是**有意跳过**而非待修缺陷；
③ autouse fixture 从类级切断 `_load_corrections` / `_save_corrections`，让整个测试文件 hermetic
（原测试会真实读写仓库根目录 40KB `data_corrections.json`，既污染运行态又拖慢 3.40s→0.92s）；
④ 把「`confirms` 读 `next_close` 为何不算 P1-10 前视残留」写成**代码内稳定契约注释** + 测试名引用，
防止下个会话看到 `next_close` 又来"修"一遍、退回 P0-2「抹平真实涨停」。

**推广**：
1. **凡签名是 `List[Dict]` 且承诺返回"加工/修正副本"的函数，`copy()` 一律是浅拷贝**，
   必须逐元素拷贝或 `deepcopy`；测试要同时断言「入参未变」和「元素对象不共享」，
   只断言返回值等于期望值**证明不了没有副作用**。
2. **台账写「未完成」不等于真有活**：本轮 ROI 项的 P1-10 早在 `b54da08`（2026-06-06）修复并回勾，
   `NEXT_SESSION_PLAN.md` 里那个 `- [ ]` 是已归档历史快照。先做实证核验再动手，
   真正的缺陷（浅拷贝污染）恰恰是核验中发现的，而不是台账上写的那条。
3. **判定"此处的未来数据是不是前视"要看语境**：事后回溯修缓存用次日收盘确认跳变真伪是必要信息，
   不是回测信号的前视偏差。把这类设计取舍写成代码内契约 + 测试守卫，比写进文档等下个人记住更可靠。

### P-78 · 删掉「被对拍基线冻结的配置文件」不改代码也会判红：基线过期 ≠ 代码回归

**现象**：L5 `scoring_baseline.py --check` 持续 `[FAIL] 34 difference(s)`，且 stash 对照证明
在 clean tree 与工作树下 diff 集合完全一致（不是当前改动引入）。全量导出后发现 34 条**全部**
是 `judge_panel.*.legacy.group_scores.AI 量化`，数值严格 ×5（0.05→0.25、0.059→0.296），
其余 8 条链路、`contract`、`kernel_raw_type` 零差异，`mismatches` 全 0。
注意 `--check` 只在 `main()` 里 `print(diffs[:30])`，**34 条里最后 4 条永远不显示**，
照着输出数会误判成「只有 30 处」。

**根因**：`4b9c2d3`（RF-02 D3，2026-11-13 18:15）删除 `config/judges.json`。
`judge_panel._load_judge_groups()` 优先读文件、缺失才回退 `_DEFAULT_JUDGE_GROUPS`，
「AI 量化」权重由文件里的 **0.20** 变成代码默认的 **1.00**。
`_get_judge_weight()` 返回 `weight/len(judges)` ⇒ 每票权重 ×5；
`group_scores = Σ(s·c·w)/count`（分母是票数，不缩放）⇒ 保留 ×5；
而 `final_score = Σ(s·c·w)/Σw` 分子分母同乘 5 抵消 ⇒ **决策字段全部不变**。
L5 基线快照最后一次 capture 是 `6cfd264`（2026-11-10，配置还在），
**RF-02 收官只跑 L1/L8/L9，没跑 L5，也没重建基线**。
ADR-0015 F4 其实已预见「AI量化 反从 1.00 降到 0.20」，决策与防回归测试
（`test_judges_json_drift_file_is_gone`）都正确 —— **缺口是流程，不是代码**。

**修复（本轮只定因，未改基线数字）**：按 G3 先证明来源，禁止改数字迎合判据。
① 全量导出 diff（绕开 `diffs[:30]` 截断）并按链路/字段归类；
② **唯一变量对照实验**：从 `git show '4b9c2d3^:config/judges.json'` 读出历史配置注入
`judge_panel.JUDGE_GROUPS`（不改基线/断言/代码），diff **34 → 0**，因果闭环；
③ 影响面实测：60 样本 `consensus`/`score`/`bullish_pct`/`bullish_count`/`total_votes`/`abstained`
**各 0 变化**，仅 `group_scores` 34 例；全仓搜索该字段无下游消费方；
④ 用 `git cat-file -e <commit>:config/judges.json` 逐 commit 定位引入点；
⑤ G4 前置验证：两次独立 `--capture` 的 `chains` 逐字节一致（sha256 相同）、9/9 链路
`mismatches=0` ⇒ 当前实现健康，重建基线可行。
完整证据链：`docs/audit/l5-scoring-baseline-drift-2026-11-13.md`。
**结论**：当前 HEAD 才是设计意图值，基线里冻结的是被漂移配置污染的旧值；
推荐按 G4 字段级取证重建基线（需用户拍板），
**不推荐**把默认权重改回 0.20（撤销 ADR-0015 D3 决策 = 迎合判据改生产代码）、
不推荐从指纹里剔除 `group_scores`（削弱门禁）。

**推广**：
1. **「对拍基线」冻结的是快照，不是真理**。配置源变更（删文件/改默认表/改权重）即使
   不改算法，也会让被冻结的展示字段漂移。判红时先问「是代码变了还是快照过期了」，
   用**唯一变量对照实验**证明，而不是 stash 一下就长期口头豁免「既有漂移」。
2. **删 `config/` 下文件必须进「跑全门禁 + 同步重建对拍基线」清单**，
   且应做成机器判据（如在基线 `constraints` 里登记配置源），否则同类失修会复发。
3. **`--check` 类工具若有 `[:N]` 截断，排查必须自行导出全量**，否则会基于不完整证据下结论。
4. 判断影响面要**逐字段实测**，别凭推理：`group_scores` ×5 看似严重，但决策字段零变化、
   无下游消费方 ⇒ 实际风险低。同时也要注意：**低风险不等于可以不修**，
   长期判红会让门禁失去信息量。
5. **修判据 ≠ 改数据迎合判据**。本例的正确收口是「把隐含外部输入（配置源）登记进
   `constraints` + 加机器判据 + 用 `--capture` 重建快照」，而不是把权重改回去、
   或把变化字段从指纹里删掉。判据要能报出**指向性信号**（`judge_groups_source changed`），
   而不是让人面对 34 行数值 diff 猜原因。

> **落地（2026-11-13，用户批准后执行）**：按 G4 用 `--capture` 重建
> `docs/refactor/scoring-baseline.json`（两次独立 capture 整份逐字节一致，sha256
> `a4a1b4ff7e078126e417cbed`；变化恰好 34 个 `group_scores` + 2 个新增 constraints 键，
> 其余 8 链路逐字节一致），**L5 恢复 `[PASS] 9 sources match (285 samples)`**。
> 机器判据落在 `scripts/scoring_baseline.py::_judge_groups_constraints()` +
> `tests/test_baseline_pinning.py` 3 例（重建前第 3 例按预期判红并给出「请重建基线」提示，
> 重建后 9/9 绿）。生产评分代码零改动。验收 **1312 passed / 12 skipped / 0 failed**，
> L0–L5 / L7–L9 全 PASS。详见 `docs/audit/l5-scoring-baseline-drift-2026-11-13.md` §5。

## 附 · 一句话索引（按症状查）

| 症状 | 去查 |
|---|---|
| 两个口径数字一样但总觉得哪里不对 | P-01 |
| 基线字段都在但全是空的 | P-02 |
| 对拍 / 自检自己就不稳定 | P-03 |
| 门禁在本机判红、CI 判绿 | P-04 / P-06 |
| 跨机器 / 跨 pandas 版本就假红 | P-05 |
| 测出来"特别慢"，但慢得不像业务逻辑 | P-07 |
| 搜出来的候选里漏了最慢的那个 | P-08 |
| 两三轮数据看不出稳定不稳定 | P-09 |
| 加了门禁但从没见它红过 | P-10 |
| Windows 上偶发 PermissionError（含 portalocker 抢开 .lock 误判「拿不到锁」） | P-11 / P-13 / P-66 |
| 并发写丢数据 | P-12 |
| 回测结果有未来数据 | P-34 / P-35 / P-36 |
| mock 了却没生效 | P-18 / P-19 |
| 某个测试"在 Windows 上跳过" | P-20 |
| 禁网后 asyncio / FastAPI 全崩 | P-21 |
| 下游降级分支没被走到 | P-22 |
| 同一个错漏了第二次 | P-23 |
| 搬完代码行为悄悄变了 | P-24 / P-25 / P-26 / P-27 |
| 合并同名函数后财务数据变了 | P-28 / P-37 |
| CI workflow 直接失败但看不出哪行 | P-39 |
| 「扫描函数体」显示干净，实际还在阻塞事件循环 | P-67 |
| 计划里的绝对阈值（≤N）根本达不到 | P-67 / ADR-0006 |
| 门禁红了但说不清是哪条判据红的 | P-10 / P-54 / P-67 |
| 没改的端点被判「基线漂移」，一查只是行号变了（这次是端点顶层 lineno 漏剥） | P-68 / P-72 |
| A/B 对拍里冒出后台预取 / 缓存预热日志 | P-69 / P-33 |
| 只给调用处补锁，下次又从别处漏进来 | P-68 推广 / ADR-0007 D4 |
| 优化后压测反而慢一个数量级 | P-70 |
| 「逐条留痕」表里遗留被改名成已完成 | P-71 |
| L9 对拍因 `datetime` 不可变类型 `patch` 崩 / 旧新语句数不对称假红 | P-73 |
| 对拍全绿但被测路径根本没执行（mock 空值撞上 `is not None`） | P-56 / P-74 |
| 守卫全绿但端点真实运行时 ImportError / AttributeError（名字存在 ≠ 符号可解析） | P-75 |
| 冻结门禁把「后续合法行为修复」一视同仁判红（无法区分悄悄改动 vs 有意分歧） | P-76 |
| 函数"返回副本"却把调用方传入的数据就地改了（`List[Dict]` 浅拷贝） | P-77 |
| 测试全绿但「台账说没做完」的功能其实早修了，真缺陷藏在没被测的副作用里 | P-77 |
| 删了配置文件后对拍基线长期判红，stash 对照却证明「不是当前改动」 | P-78 |
| `--check` 输出的 diff 条数比实际少（脚本 `[:30]` 截断） | P-78 |
| A/B 对照里 OpenAPI / schema 比较恒等 | P-54 / ADR-0009 D3 |
| Actions 额度消耗太快 | P-40 |
| commit 失败却把旧代码推上去了 | P-43 |
| CI 红了但看不出是哪道门禁 | P-42 |
| 改动根本没进仓库 | P-43 / P-48 |
| 基线改都没改就自己判红 / 数字随日期变 | P-52 |
| 计划写「N 份重复实现」但越合并越不对 | P-53 |
| 对拍全绿但感觉没锁住任何东西 | P-54 |
| patch 类方法还原后多出 self / 报参数错 | P-55 |
| 开关前后 0 差异 / 灰度好像没生效 | P-56 |
| 新切的消费者好像从没被测到 | P-57 |
| 清单里的「消费者」只是转发，没什么可切 | P-58 |
| 端点没切点 / diff 恒 0 但说不清为什么 | P-59 |
| 验收时冒出一个跟本次改动无关的红灯 | P-60 |
| 拆大文件前说不清原来有哪些端点/鉴权 | P-61 |
| AST `FunctionDef.lineno` 指向 `def` 行而非装饰器行，按它切会把 `@app.xxx` 截断 ⇒ 0 路由 | P-62 |
| 端点函数搬进子包后 `__file__` 落到 `routers/`，`Path(__file__).parent` 让静态/缓存目录漂移 | P-63 |
| `app.include_router` 后顶层是 `_IncludedRouter`（无 `.routes`），门禁遍历直接丢端点 | P-64 |
| 顶层模块 `api.py` 用 `from .routers...` 相对 import 必崩 `no known parent package` | P-65 |
| portalocker 的 acquire() 重试循环只覆盖「加锁 syscall」不覆盖「打开 .lock 文件」，Windows 多进程抢开同一 .lock 间歇 PermissionError 逃出重试 → 误判「拿不到锁」 | P-66 |

---

## 附 · 本文档的证据来源

| 证据 | 位置 |
|---|---|
| 逐批执行记录 / 验收表 / 复盘原文 | `docs/plans/2026-11-07-refactor-plan.md` 第七节 |
| 基线读数（规模 / 契约 / 覆盖率 / 耗时） | `docs/refactor/baseline.md` |
| 尺子口径与自证记录 | `docs/refactor/README.md` |
| 契约快照（72 条路由） | `docs/refactor/contracts/route-contract-baseline.json` |
| 覆盖率基线（三平台，各 141 模块） | `docs/refactor/coverage-baseline.json` |
| 双跑对拍基线（3 链路） | `docs/refactor/dual-run-baseline.json` |
| 耗时与稳定性基线 | `docs/refactor/test-duration-baseline.md` |

**文档状态**：2026-11-07 首版（Phase 0 → Phase 3，51 条）；2026-11-10 追加 P-52 / P-53（Phase 4 前置批次）
与 P-54 / P-55（阶段 A）+ P-56～P-60（阶段 B）+ P-61 / P-62～P-65（Phase 5 拆 api.py 单体）+ P-66（portalocker 打开争用重试）；
2026-11-11 追加 P-67（Phase 6 第 1 批：阻塞 IO 扫描是空判据 + 绝对阈值被实测推翻），
P-68（lineno 进基线使逐字节比较不可执行）与 P-69（导入期 HS300 预取污染 A/B 取证）；
再追加 P-70（异步压测协程内计时被调度方式带偏）与 P-71（人工分类会把遗留改名成已完成），共 **71 条**；
2026-11-11 **Phase 6 第 4 批**：追加 P-72（L8 端点顶层 lineno 漏剥，recapture 误报 21 条漂移的根因修）
与 P-73（L9 冻结 datetime.now 用 Mock(wraps=datetime) 按命名空间 patch，攻克 C 扩展不可变类型），
共 **73 条**。
2026-11-11 **Phase 6 第 5 批**：追加 P-74（对拍 mock 返回 falsy `{}` 撞上端点 `is not None` 缓存判断，
on 侧根本没进 `run_blocking`——响应 diff 恒 0 的「假通过」，靠线程池自证兜住），共 **74 条**。
2026-11-12 **Phase 6 第 7 批**：追加 P-75（结构守卫钉「名字出现在函数体」，坏 import 的名字
也算数——3 个端点真实运行时 ImportError/AttributeError 却长期全绿），共 **75 条**。
2026-11-13 **RF-02 / ADR-0015**：追加 P-76（迁移冻结门禁 vs 有意行为修复——L9 `check_migration` 把
offload 后 `_compute_*` 体逐字冻结为等价判据，RF-02 的 D1/D2 修复恰好改动这两个冻结体而判红；
经用户确认**演化门禁、不削弱**：新增 `MIGRATION_DIVERGENCES` 清单对 2 个分歧迁移豁免逐字等价、
仅校验接线完整性，行为正确性由 `tests/features/test_rf02_bad_endpoints.py` 兜底），共 **76 条**。
**核对基线**：本地全量 pytest 1162 passed / 12 skipped（13.02s）；CI run `42319502027` 3 job 全绿。
2026-11-10 阶段 A 收官：**1221 passed / 12 skipped**，L0–L5 全 PASS。
2026-11-10 阶段 B 首批：**1235 passed / 12 skipped**。
2026-11-10 阶段 B 第 3 批：**1242 passed / 12 skipped**。
2026-11-10 阶段 B 第 4 批：**1249 passed / 12 skipped**。
2026-11-10 **Phase 4 收官**：**1253 passed / 12 skipped**（串行干净复跑）；
L0–L6 全 PASS（L6 = `realtime` / `no_realtime` / `endpoint` 三分支 + 消费者级自证）。
既有间歇性用例 `test_signal_tracker.py::TestBorrow2AtomicWrite` 的 portalocker 抢开 `.lock` 瞬时 `PermissionError` 已**根因修复**（P-66 + 回归测试 `TestPortalockerOpenContentionRetry`），不再间歇失败；P-60 仍作为「收官被无关红灯打断别急着改断言」的纪律条目保留。
2026-11-10 **Phase 5 第 2 批收官（拆 api.py 单体）**：`api.py` 3087→~200 行装配层，68 端点按 17 域落 `routers/`；
**1262 passed / 12 skipped / 0 failed**，L0–L7 全 PASS（L2 72 routes diff=0；L7 68 端点/17 router/36 前端路径，
auth+async+contract 不变）。新增 P-62～P-65（AST 装饰器截断 / `__file__` 漂移 / `_IncludedRouter` 遍历 / 顶层模块相对 import）；
P-66 修复 portalocker「打开 .lock 文件」瞬时 PermissionError 逃出重试循环 → 门户层 `_acquire_portalocker_with_retry` 把打开/加锁争用统一重试，新增 2 例回归测试。
决策见 `docs/adr/0005-phase5-api-split.md`。
2026-11-11 **Phase 6 第 1 批（零业务代码改动）**：新增第 8 把尺子 `scripts/blocking_io_check.py` +
`docs/refactor/contracts/blocking-io-baseline.json` + `tests/test_blocking_io_check.py`（11 例）。
实测 68 端点 / 1052 调用 / **242 阻塞调用** / 63 端点含阻塞点 / 1 已 offload；
helper 展开额外捕获 95 次调用（19 端点）。计划「≤1」判据按第 6 次修正改为单调下降 + 分层，
决策见 `docs/adr/0006-phase6-blocking-io-criteria.md`；新增 P-67。
第 1 批验收：**L0–L8 全 PASS，1275 passed / 12 skipped / 0 failed（54.95s）**；
L2 72 routes / L3 3 链路 / L4 全链路 / L5 9 入口 285 样本 / L6 3 分支 9 响应 0 差异 /
L7 68 端点 17 router 36 前端路径 / L8 242 blocking + selftest 5 场景全绿。
2026-11-11 **Phase 6 第 2 批**：`FEATURE_ASYNC_OFFLOAD`（默认 off）+ `lib/infra/async_offload.py`
唯一入口；3 个 Tier A 回测端点 offload（固定 commit `523b353` AST 逐语句等价）；
`_scan_tasks` / `_perf_metrics` 收口到带锁 API（AST 守卫防回退）；新增第 9 把尺子 L9；
L8 基线两次按 G4 重建并留历史证据（blocking 242→228、offloaded 1→4）。
新增 P-68 / P-69；决策见 `docs/adr/0007-phase6-offload-and-locks.md`。
第 2 批验收：**L0–L9 全 PASS，1305 passed / 12 skipped / 0 failed（76.23s）**；
L2–L7 读数同上不变 / L8 228 blocking、4 offloaded + selftest 全绿 /
L9 3 端点迁移 AST 等价 + off/on 整份响应 0 差异 + 线程池 off=0 on=1（工作线程与主线程不同）。
2026-11-11 **Phase 6 第 3 批**：TTL 缓存按 ADR-0008 裁剪（既有 `/api/scan` 缓存证据冻结）；
第 10 把尺子 L10 并发压测（首次因「协程内计时」误报慢 10 倍 ⇒ 改批次发起计时，
修判据不放宽阈值，P-70）；36 条前端路径改契约冻结（清空 `app.openapi_schema` 防空判据）；
Tier B 201 条机械分类逐条留痕、0 未分类，35 个 Tier A 候选端点显式列为遗留（P-71）。
新增 P-70 / P-71；决策见 `docs/adr/0008-*.md` / `0009-*.md`。
第 3 批验收：**L0–L10 全 PASS，1312 passed / 12 skipped / 0 failed（61.93s）**；
L10 off P95 389–404ms → on 208–220ms（3/3 改善），36 条路径 + off/on OpenAPI 冻结。
2026-11-11 **Phase 6 第 4 批（Top5 端点 offload + 两处门禁判据根因修）**：5 个 Tier A 候选端点
（`factor_eval`/`factor_scatter`/`kline`/`risk`/`export_config`）切到 `run_blocking`；
L9 扩至 8 迁移、L10 扩至 8 端点（8/8 P95 改善）；门禁两处判据根因修（P-72 / P-73）；
Tier A 候选 **35 → 30**；**1312 passed / 12 skipped / 0 failed**；L8 188 blocking、9 offloaded、selftest 全绿。
**收官前核验结论：Phase 6 未收官**（30 个 Tier A 候选端点未 offload，ADR-0010 D6）。
后续每批收官时**追加**新条目并在本行更新版本，不要重写已有条目 ——
**踩坑记录的价值在于保留了当时的错误判据与被推翻的结论**。
2026-11-13 **P1-10 收尾（数据修正器）**：核验「历史数据回溯修正」时发现
`correct_data` 的 `data.copy()` 浅拷贝污染入参（P-77），根因修为逐元素拷贝 + 4 例机器判据；
另把「`confirms` 读 `next_close` 不算前视残留」写成代码内稳定契约。**L0–L4 / L7–L9 全 PASS，
1309 passed / 12 skipped / 0 failed**（基线 1305 + 新增 4 例）；L5 仍为既有 34 differences
（与 2026-11-13 扫描性能修复时的 stash 对照结论一致，非本批引入）。
2026-11-13 **L5 34-diff 定因（本轮只取证、不改基线）**：根因 = `4b9c2d3`(RF-02 D3) 删
`config/judges.json` ⇒ judge_panel「AI量化」权重 0.20→1.00 ⇒ 展示字段 `group_scores` ×5；
唯一变量对照 diff 34→0，60 样本决策字段零变化、该字段无下游消费方，
G4 两次独立 capture 逐字节一致 ⇒ **基线过期非代码回归**，缺口是「删被基线冻结的配置未同步重建 L5」。
新增 P-78；定因报告 `docs/audit/l5-scoring-baseline-drift-2026-11-13.md`、
ADR-0015 Consequences「后续发现」段。重建基线按 G4 须用户单独拍板。
