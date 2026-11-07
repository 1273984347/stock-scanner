# RF-01 踩坑集（工程失败模式与推广）

> **来源**：2026-11-07 RF-01 分阶段重构（Phase 0 → Phase 2）的真实执行过程。
> **范围**：只收录**本项目实际踩到过**的坑，每条都带可复算证据；不含"可能会踩"的泛泛而谈。
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
是头号慢源 —— 它文件内**不直接出现** sleep / requests / akshare 关键词，只是**间接**调用 `lib/scorecard.py`。

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

**根因**：`lib/signal_tracker.py` 是**模块级** `import portalocker` ⇒ 已绑定到模块属性，
改 `sys.modules` 里的 key 改不动那个已绑定的引用。

**修复**：改用 `patch("lib.signal_tracker.portalocker", mock_pl)`。

**推广**：**patch 的目标是"使用方模块的属性"，不是"被导入的模块"。**
判据：`from X import f` 与 `import X` 都已把引用绑到使用方模块上。

---

### P-19 · patch 目标必须是**使用方**

**现象**：对拍框架 patch `lib.strategies.get_zt_candidates` **不生效**，真实策略被执行并报错。

**根因**：`lib/pipeline.py:23` 是模块级 `from lib.strategies import (...)` ⇒ 已绑定引用。

**修复**：必须 patch `lib.pipeline.get_zt_candidates`。

**推广**：**改"定义方"无效，要改"使用方"的引用。**
与 P-18 同源：都是"import 时绑定"造成的。
配套：框架必须记录 **traceback**（不只存类型 + 消息）——
只存类型和消息定位不到位置，加上 `traceback.format_exc()` 后一眼看到
`lib/pipeline.py:841 regime_scalar[1]` 期望 (min, max) 二元组却给了 float。

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

**现象**：`from lib.perf_metrics import _perf_metrics` 绑定的是**列表对象**
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
（`lib/data_utils.py` 的 to_float(v, strip_grouping, strip_cn_units, zero_is_none,
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

**修复**：改为 `lib/api_core.py`（符合项目 lib/ 惯例）。

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
| Windows 上偶发 PermissionError | P-11 / P-13 |
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
| Actions 额度消耗太快 | P-40 |
| commit 失败却把旧代码推上去了 | P-43 |
| CI 红了但看不出是哪道门禁 | P-42 |
| 改动根本没进仓库 | P-43 / P-48 |

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

**文档状态**：2026-11-07 首版，覆盖 Phase 0 → Phase 2 全部实测踩坑（48 条）。
**核对基线**：本地全量 pytest 1162 passed / 12 skipped（13.02s）；CI run `42319502027` 3 job 全绿。
后续每批收官时**追加**新条目并在本行更新版本，不要重写已有条目 ——
**踩坑记录的价值在于保留了当时的错误判据与被推翻的结论**。
