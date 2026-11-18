# RF-01 踩坑集（工程失败模式与推广）

> **来源**：2026-11-07 RF-01 分阶段重构（Phase 0 → Phase 3）的真实执行过程。
> **范围**：只收录**本项目实际踩到过**的坑，每条都带可复算证据；不含"可能会踩"的泛泛而谈。
> **规模**：**94 个编号（P-01～P-94）**，分九类；其中 90 条有独立正文条目，
> P-62～P-65 仅见于下方「一句话索引」（既有状态，append-only 不补写）。
> **本行是当前编号数的唯一真源**：其它文档一律引用本行，不硬编码数字；
> 文末「文档状态」里的历史「共 N 条」是当时的取证记录，按 append-only 保留。
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

### P-79 · 复合返回值悄悄取代裸映射：CLI 展示/推送链断流 6 个月，L1 全绿毫不知情

**现象**（2026-11-14 实盘扫描实测）：`python run.py --symbols ...` 扫描出 25 只候选股，
控制台却在打印结果时崩 `AttributeError: 'dict' object has no attribute 'drop'`
（`run.py:122 → reporter/scan_notify.py::print_results`）。回查
`cache/reports/push_log.json`：末条推送 **2026-05-30 16:38 UTC**，之后 6 个月零推送——
与引入该调用链的 commit `b6d63b2`（V6 自适应交易系统，2026-05-30）同日。

**根因**：`pipeline.run_all_strategies` 返回**复合 scan_result**
（`results/risk_check/portfolio/ml_ranking/...` 九键），而 CLI 侧 `print_results` /
`send_feishu_message`（`build_message_content`）按**裸 `{策略名: DataFrame}` 映射**消费：
复合 dict 一迭代，`portfolio` 键撞 `'dict' has no 'drop'`、`preset` 字符串撞 `'str' has no 'empty'`。
Web 路径无恙——`routers/common.py:264` 一直正确解包 `scan_result.get("results", {})`，
两个消费入口两种形状全靠调用方自觉，没有机器判据钉住。崩点还在 `if not results` 之后：
复合 dict 永远真值 ⇒ 空扫描日也不再走「无候选股」分支；崩掉的是 CLI 主流程其后所有步骤
（推送、IC 面板、组合优化）。IC/组合优化块里已有 `hasattr(df, "empty")` 防御——
说明有人嗅到过形状问题，但没修根因（`print_results` / 解包），防御只救了自己那一小块。
`pipeline.py:426` 还第三种形状给 `send_feishu_message` 传警报 dict
（`{"type": ..., "alerts": [...]}`）→ 同样不匹配，被 try/except 静默吞掉。

**修复**：根因修在**边界解包**，不给消费层塞防御——`reporter/scan_notify.py` 新增
`unwrap_scan_results()`（复合→取 `results`；裸映射→原样；非 dict→空映射），
`run.py` 两处调用（主扫描 `:115` / 调仓 `:928`）改经它解包，与 Web 路径解包语义一致。
**7 例机器判据**（`tests/test_scan_display_contract.py`）：解包三种形状 + 解包后
`print_results` / `build_message_content` 端到端输出（逐策略只数、汇总数、空策略不计入）+
裸映射回归钉。警报 dict 的**内容层**适配（现在静默失败）登记为独立 TODO，不与本批混做。

**推广**：**同一函数被两个入口消费、两个入口拿到的形状不一样时，必须有一把尺子钉住
「每个入口该传什么形状」**——契约靠调用方自觉维持，漂移只是时间问题。
尤其危险的是「返回值升级」（裸映射→复合 dict）：旧的直接消费方不会编译报错，
只会在**运行到那行时**炸，而炸点（打印/推送）往往不在测试覆盖内。
证据链技巧：**输出产物的时间戳就是断流的时刻**（push_log 末条 vs 引入 commit 同日），
比读代码猜"什么时候坏的"可靠得多。

**落地（2026-11-14）**：L1 全量 **1321 passed / 12 skipped / 0 failed**（基线 1312 + 本批 9 例）。

### P-80 · 测试套件把 mock 行情写进生产缓存：L1 全绿 ≠ 运行时数据无害

**现象**（2026-11-14 实盘扫描实测）：23:50 跑全量测试套件（P-78 收尾批次）；
00:08 真实扫描（12 只 HS300 蓝筹）出现三个症状——量价突破/多因子选股
`KeyError: '成交量'`（两个策略零候选）、`cache/realtime_realtime.parquet`
内容是 **「股票1」+ 随机价格（000001 @ 81.77）**、fundflow 磁盘缓存 0 条。

**根因**：三层叠加的污染链——
① `tests/conftest.py` autouse fixture 把 `ak.stock_zh_a_spot` mock 成 100 行
「股票N」假行情（禁网治理 P-21 时代的产物）；② 某测试经**真实**
`data_layer.get_realtime_quotes` 走到 pytdx 失败（禁网 socket 阻断）→ akshare 兜底
→ 拿到 mock → `_save_cache("realtime/realtime", df)` 落盘，而 `CACHE_DIR` 是
**模块级仓库相对路径**（生产与测试共用）；③ 盘后缓存 TTL 30 分钟，
40 分钟后的真实扫描正中弹着时间窗。后果：mock 缺 `成交量` 列 ⇒ 契约消费方
（`strategies.py:362/645/758`）KeyError；假价格流入 ML/评分（该次扫描作废）。
L1 全绿是因为毒缓存**写入**不是任何测试的断言对象——缺陷潜伏在断言的盲区。

**修复**：① conftest 新增 autouse `_isolate_repo_cache_dirs`：把
`data_layer.CACHE_DIR / FIN_CACHE_DIR`、`lib.data.cache.CACHE_DIR`、
`data_cache._CACHE_DIR`、`api_core.CACHE_DIR` 全部重定向到 pytest tmp——
**测试永不写生产缓存**成为默认机制而非逐个测试的自觉（P-23 教训：同一类遗漏
要改默认机制，不是逐个补）；② `_mock_akshare_stock_zh_a_spot` 补 `成交量` 列，
对齐 `get_realtime_quotes` docstring 契约与真实 akshare 14 列 schema；
③ 删除毒缓存 `cache/realtime_realtime.parquet`。
**2 例机器判据**（`tests/test_conftest_cache_isolation.py`）：fixture 重定向生效 +
端到端复现链回归钉（pytdx import 失败 → akshare mock → 落盘进 tmp、仓库 cache/ 零写入、
mock 契约列齐全）。

**推广**：**mock 掉「数据来源」不等于隔离了「数据去向」**——凡是被 mock 的取数函数
若仍会执行真实的**写路径**（缓存落盘、文件追加、DB insert），假数据就会流进生产存储。
判据有三：① 测试基础设施的重定向要做成 autouse **默认机制**，覆盖面 = 所有缓存/持久化
根路径模块属性；② mock 数据必须满足**被测函数 docstring 承诺的返回契约**（缺列 =
把 schema 漂移伪装成测试环境常态，真实上游漂移时反而无感）；③ 「L1 全绿」只是
断言盲区内的绿——对「测试会写哪些路径」要单独审计一遍（`_save_cache` /
`NOTIFICATION_DIR` / push_log 一类的写入口点名排查）。

**落地（2026-11-14）**：同上批 L1 **1321 passed / 12 skipped / 0 failed**；
套件跑完后生产 `cache/` 实测零新增文件。


### P-81 · `monkeypatch.setattr("模块A.time.sleep", ...)` 改的是全局 time 模块：sleep 型测试桩被静默拆掉

**现象**（2026-11-14，ADR-0019 机器判据首版 3 例假失败）：为验证「数据源挂起 → 超时熔断」
写了 sleep 型挂起桩（`time.sleep(5)` 后返回空帧），在 pytest 下瞬间返回——超时熔断逻辑
被判「没生效」，但同样的桩在 pytest 外复现完全正常。conftest 第 293 行
`monkeypatch.setattr("lib.data.data_layer.time.sleep", lambda *a, **k: None)` 是真凶。

**根因**：`lib.data.data_layer.time` 不是独立模块——data_layer `import time` 后，
该属性就是**全局 time 模块对象本身**。monkeypatch.setattr 解析目标是
「`lib.data.data_layer.time` 模块的 `.sleep` 属性」⇒ 实际改写 `time.sleep` 全局属性，
整个测试进程的所有代码（包括测试自己的桩、registry 的 worker 线程）都看到 no-op sleep。
原意（压掉 data_layer 重试的 sleep 等待）与实际效果（全局拆 sleep）之间没有隔离层。

**修复**：挂起桩改用 `threading.Event().wait(5)`（线程原语，不在 patch 面）；
ADR-0019 的机器判据 4 例全绿。conftest 的 sleep patch 本身保留（压慢测试是它的职责），
但从此知道它的真实作用域是全局。

**推广**：**monkeypatch 模块属性前先问「这个属性是共享对象还是模块私有」**——
`import time` 后的 `模块A.time` 是全局单例，`patch("A.time.sleep")` 等价于
`patch("time.sleep")`，影响面 = 全进程。判据：给「阻塞性测试桩」选原语时避开
已被 patch 的全局面（time.sleep / datetime.now / builtins.open 一类），优先用
线程/事件原语（Event.wait、queue.get(timeout)）或把桩的阻塞点做成可注入参数。
写「模拟挂起/慢响应」的测试前，先确认阻塞原语在测试环境里真的会阻塞。


### P-82 · 财报「点内过滤」按会计报告期 `end_date` 裁 `as_of`：回测提前数月吃到年报

**现象**（2026-11-14，审查未合并分支 `feature/cycle-top-warning`）：分支
`lib/cycle_heat.py:8` 与 `backtest/engine.py` 顶部预警注释均声明「回测点内、无未来函数」，
提交 `f340da0` 据此登记「A/B 改善 58.5%」。但 `lib/data_tushare.py` 三处
`fields="ts_code,end_date,..."` 不含 `ann_date`，`lib/data_industry.py:62`
`{"date": q["end_date"].astype(str)}` 把报告期直接当时间轴，`:98-108` 的
`_AK_BS_MAP` / `_SINA_BS_MAP` 等映射表也无披露日列，`lib/cycle_heat.py:80` 按
`s[s["date"] <= str(as_of)]` 过滤。

**根因**：`end_date` 是**会计报告期**，不是**信息可获知日**。A 股年报
`end_date=20231231` 的法定披露可到次年 4 月，`as_of=20240101` 的回测就能读到它。
「无未来函数」这句声明实际只约束了价格链（`backtest/engine.py` 的
`hist_x = dfx[dfx["trade_date"] < date]`），**没有约束财报链**——两条链口径不一致，
声明却整句覆盖。价格链的点内取证被当成整个策略的点内证明。

**修复**：三源统一补披露日（Tushare 取 `ann_date`、东财取 `NOTICE_DATE`；
确无该列的源用显式 `REPORT_LAG_DAYS` 常量并写明依据），过滤改
`ann_date <= as_of`，`end_date` 仅用于排序；缓存 key 带数据版本。
新增 `test_no_lookahead.py`：构造 `end_date=20231231 / ann_date=20240425` 的财报表，
断言 `get_block_cycle_heat("白酒", as_of="20240101")` 不含该期；
**A/B 收益数字必须在口径修完后重跑，修前登记的 58.5% 作废**。

**推广**：**「无未来函数」必须逐条数据源列出 `as_of` 究竟绑在哪个日期字段**，
不能由「价格链点内」外推到「财报链点内」。P-34/35/36 是签名层前视
（参数收了却仍用 `now()`），本条是**语义层前视**（参数用对了、字段选错了），
后者更难被静态检查发现。判据：任何新增外部数据序列接入回测，先写一条
「报告期 vs 披露日」反例单测，再讨论收益数字。

**实证补记（2026-11-14，把上条从「制度推断」升级为可复算证据）**：用
`git show feature/cycle-top-warning:lib/cycle_heat.py` 取**真实源码 150 行**动态加载，
只把 `config.CycleWarningConfig` 与 `lib.data_industry` 的两个取数函数换成桩，
**过滤与分位逻辑一行未改**。构造 40 期季度序列（2014Q1–2023Q4，三指标 yoy
前 39 期交替 0.1/0.9、末期 0.99），末期 `end_date=20231231`、按年报披露滞后
设 `ann_date=20240430`；在 `as_of="20240101"` 调真实 `get_block_cycle_heat("白酒")`：
- 当前实现按 `date <= as_of` 纳入 **40 期**，末期是
  `end_date=20231231 / ann_date=20240430`（4 个月后才披露），
  得 `hot_count=3`、三指标分位均 `1.0`；
- 把取数桩换成「按 `ann_date <= 20240101` 真实可见的 39 期」后，
  得 `hot_count=0`、三指标分位 `0.5128`；
- 破位为 True 时（`config.py` 的 `hot_count_exit=2`），动作 **`exit_all` → `hold`**。

即前视数据不只是"存在"，而是**能凭空造出清仓信号**。复算零联网、零项目文件改动，
只依赖 `pandas` 与 git 对象。**结论：`f340da0` 的 A/B 改善 58.5% 不能作为合并依据。**

**现役对照（同日核查）**：master 并非没有防前视机制——
`tests/test_datalayer_p1_2_asofdate.py:22-50` 已是机器判据，强制
`_get_financial_data` 带 `as_of_date`、不传即 `raise NotImplementedError`；
`lib/data/data_sina_fin.py:95,121,147` 的 `end_date=f"{year}1231"` 是**请求查询区间**、
返回聚合 dict 无时间轴，性质不同，**不构成同类前视**。
所以 P-82 的根因不是"项目缺机制"，而是**新增财报序列没接既有 `as_of_date` 契约**——
与 P-84「新链路绕开 registry」同构。改法应复用该契约并把它扩到序列型财报数据，
而不是另造一套；门禁应能回答「新增序列型数据源归哪条 `as_of` 契约管」。

**字段与量化补记（2026-11-14，接口实证）**：东财 datacenter
`RPT_F10_FINANCE_GBALANCE` 实际返回 `REPORT_DATE` / `NOTICE_DATE` / `UPDATE_DATE`，
样例 2026 三季报 `REPORT_DATE=2026-09-30` → `NOTICE_DATE=2026-10-31`（滞后 31 天），
**改法不需要猜字段**。取 600519 全量 104 期（1998Q4–2026Q3）统计：
Q1 中位 25 天、H1 43 天、Q3 26 天、Q4/年报中位 109 天；
近期子集（2015 起 47 期）Q1 26.5 / H1 34 / Q3 26 / 年报 90 天，
**前视窗口占日历天数 49.8%（2109 / 4233）**；全时段 70.4% 被早期年报
max 938 天拉高，不宜引用。
再用真实滞后结构 + 分支真实 `cycle_heat.py` 做 37 个调仓日
（2023-01-01–2024-12-31，每 20 天）信号层对照：`exit_all` 前提命中
**修前 18 次（首次 20240116）→ 修后 13 次（首次 20240425）**，
**5 个调仓日的信号纯由未披露年报提前触发**（20240116 / 0205 / 0225 / 0316 / 0405）。
即前视的作用不是「信号凭空存在」，而是**把逃顶信号提前约 90 天**——实盘做不到。
注意：数值序列为合成、滞后结构取自真实接口，该结论是**机制层面的可复算证据**，
不是「收益下降 X%」；端到端 A/B 为何不可复算见 P-85。

**字段语义对照补记（2026-11-14，连接器实证）**：改用 MCP 取数时，
**字段名相近、语义却相反**，直接接进 `cycle_heat.py:80` 的过滤会再犯一次前视：

| 数据源 | 报告期字段 | 披露日字段 | 取证状态 |
|---|---|---|---|
| 东财 datacenter（被审代码所用） | `REPORT_DATE` | `NOTICE_DATE` / `UPDATE_DATE` | **已实测**：`columns=ALL` 默认返回，样例滞后 31 天 |
| NeoData `mcp__neodata__income_statement` | `end_date` | `report_date`（= 财务报告发布日期） | **已实测**：600519 `2025-FY` 为 `end_date=20251231` → `report_date=20260417`，滞后 107 天 |
| Tushare `balancesheet` | `end_date` | `ann_date` | **代码事实**：分支 `lib/data_tushare.py` 的 `fields` 未取该列；`ann_date` 为该接口标准字段，**本次未做接口实证** |

**两个坑**：① NeoData 工具说明写了「发布日期」，但**默认返回不带该字段**——
必须在 `output_fields` 显式指定才出现 `report_date`；不实证就会以为已经拿到了公告日
（与 P-85「前置数据为空要判红」同源）。
② `report_date` 字面像「报告期」，实际是**发布日期**；而分支里的 `end_date` 是报告期。
把 `report_date` 当报告期、或把 `end_date` 当可见日，都会原样重演 P-82。
**判据**：接入任何新数据源时把字段语义写进代码契约——`as_of` 只允许与
`publish_date` 比较、禁止与 `end_date` 比较，并加一条
「同一报告期 `publish_date > end_date`」的断言；过滤前先自证披露日字段非空。
该 107 天与东财统计的近期年报中位 90 天 / 全时段 109 天吻合，
等于对 P-82 的披露滞后结构做了一次**独立交叉验证**。
另注：审查取证仍须对准被审对象——MCP 的标准化 schema 只能证明「MCP 有公告日」，
不能证明「被审代码有没有取到」，故本条只作字段对照，不替代 P-82 的接口实证段。


### P-83 · 拆包后合并长命分支：Git 跟随 rename 救得下「改动文件」，救不了「新增文件」

**现象**（2026-11-14，`git merge-tree --write-tree master feature/cycle-top-warning`）：
merge-base `a7c2265`（2026-07-04），master 已领先 77 提交完成 RF-01 八子包拆分。
结果 tree 的 `lib/` 根新增 `cycle_heat.py` / `data_industry.py` / `top_warning.py`，
三者内部仍是 `from lib.data_layer import ...`（`top_warning.py:98`）、
`from lib.market_utils import ...`（`:46`）、`from lib import data_tushare`
（`data_industry.py:228`）。master 上这些扁平路径**全部 ABSENT**，实际在
`lib/data/`、`lib/risk/`、`lib/scoring/`，合并后必然 `ModuleNotFoundError`。
同时 merge-tree 只报 4 处冲突，看起来「冲突很少、容易合」。

**根因**：分支在拆包**之前**开出，新增模块按当时的目录约定写。Git 的 rename
detection 只把**双方都存在的同名内容**映射到新路径（本次
`lib/position_manager.py` → `lib/risk/position_manager.py` 等被 Auto-merging 成功），
对**分支独有的新文件**没有「按新约定归位」的任何信息，只能原样落在旧位置。
既有机器判据 L7（端点归属）/ L8（阻塞 IO）作用域是 `routers/`，
**不覆盖 `lib/` 新增文件落点**——RULES.md D1 只是文档规约，没有 AST 守卫。

**修复**：长命功能分支不直接 merge，在 master 上开新分支按文件精选移植；
新增模块按 RULES.md §1.3 归属表落子包，import 改绝对新路径（I1/I3），
测试 patch 目标按 I4/G2 同步改；把「`lib/` 根新增 `.py` 即判红」写成机器判据，
而不是再往 D1 后面加一句提醒。

**推广**：**目录重构后，「旧分支上的新增文件」比「旧分支上的改动文件」更危险**——
改动文件会被 rename detection 救下，新增文件不会。判据：合并前用
`git merge-tree --write-tree` 取结果 tree，对**新增路径**单独跑一次目录约定校验，
不要只看冲突列表；冲突数少 ≠ 结构兼容。


### P-84 · 刚收口挂起熔断，新数据源又从 registry 之外把扫描卡死（P-68 同构复发）

**现象**（2026-11-14）：HEAD `65fdf7b`（ADR-0019）刚为 6 个网络源加
`_call_source_with_timeout`（daemon 线程 + `join SOURCE_CALL_TIMEOUT_S=30s`），
并给 `CircuitBreaker` 加 `record_failure()` 把挂起计入失败，收口 BaoStock
socket 无超时挂起卡死扫描 10+ 分钟。同日审查发现待合并分支新增的三条取数链路
全部在 registry 之外：`lib/data_tushare.py::_fin_request`（Tushare SDK 调用无超时）、
`lib/data_industry.py:149`（东财 datacenter 有 `timeout=20` 但无 `raise_for_status`、
无重试退避）、`:171-190`（akshare 新浪 / 东财逐期无超时封装）。
`_FIN_ACCESS_DENIED` 是无 TTL、无半开、无复位的全局 bool，靠
`("没有接口", "访问权限", "doc_id=108")` **错误文案字符串**判权限。

**根因**：ADR-0019 修的是**当时已登记的 6 个源**，判据是「这 6 个源接了超时」；
没有任何机器判据要求「**新增网络源必须登记**」。于是同一个挂起卡死缺口从新链路
原样复现。这是 P-68「只给调用处补锁，下次又从别处漏进来」在数据源域的同构复发，
也是「修症状不问机制」的代价。

**修复**：把「网络 IO 调用点必须经 registry / `run_blocking`」做成 AST 硬守卫
（对照 L8 对 `routers/` 的 `other` 冻结），新源未登记即判红；
`_FIN_ACCESS_DENIED` 换成带半开探测的 `CircuitBreaker`，权限判定用 SDK 错误类型
而非文案；挂起测试桩按 P-81 用 `threading.Event().wait()`，不用 `time.sleep`。
验证：仿 `tests/test_source_call_timeout.py` 挂起 `pro.balancesheet`，
断言 30s 内返回、5 次后 fail-fast，且该文件现有 4 例保持全绿。

**推广**：**修完一类故障后，先问「还有哪些入口能产生同类故障」，并把答案变成机器判据**，
而不是只修触发事故的那一个入口。判据：新增 `requests.get` / SDK 取数 / 文件 IO
调用点时，门禁必须能回答「它归哪个统一入口管」；答不上来就该判红。


### P-85 · 本地数据被删后 A/B 结论不可复算，且功能恒不触发（P-56 空判据换个马甲复发）

**现象**（2026-11-14，按合并决策审查尝试重跑 `scripts/backtest_top_warning_ab.py`）：
在独立 worktree 检出 `feature/cycle-top-warning@f340da0` 后，
`get_stock_blocks("600519") -> []`（0 秒）、`get_all_blocks() -> {}`；**master 侧同样为空**。
追到 `lib/data_layer.py:1018-1029` `_load_block_mapping()`：板块映射唯一来源是
`TDX_REF_PATH / "infoharbor_block.dat"`（默认 `D:/TDX/vipdoc/ref/`），该文件随
2026-11 用户删除本地 TDX 客户端（提交 `8025c95`）一并消失；主仓 `cache/` 34 个
文件中无板块/行业数据，`D:/TDX*` 已不存在。worktree 同时缺 `.env`（TUSHARE_TOKEN）
与 `cache/`（财报 pkl / 行情缓存）。

**根因**：两层。
① **功能层**：`lib/top_warning.py:101-104` `blocks = get_stock_blocks(code) or []` 且
`if not blocks ...: return None` ⇒ 板块为空时顶部预警**恒不触发**，
`backtest/engine.py` 的 `top_warning_enabled=True` 分支拿到的 `warn` 恒为 None，
A/B 的 off / on 两次回测结果恒等。这是 P-56 / P-59 空判据的另一副面孔：
不是「开关没切」，而是「功能的前置数据没了」，diff=0 长得一模一样。
② **取证层**：`f340da0` 登记的「A/B md 改善 58.5%」是在**仍有 TDX 本地数据**的
旧环境跑出的，该环境已不可重建；任何重跑都不是同口径对照。
把「补数据让 A/B 跑起来」当解法是补丁——会构造一个生产环境并不存在的假环境。

**修复**：本次**不改分支代码、不重跑端到端 A/B**，直接把分支判为放弃移植
（前置依赖已不存在，补 `ann_date` 也换不来可触发的功能）。
若将来在 master 上重建行业周期能力，前置条件是：
① 板块映射改不依赖 TDX 本地文件的源，并给「映射为空即显式报错」的机器判据；
② A/B 取证脚本把环境依赖（凭证 / 缓存 / 板块源）写进结果 JSON，让「结论在哪个环境成立」可复算；
③ 按 P-82 补 `ann_date` 口径之后才谈收益数字。
替代判定用可复算的信号层对照完成（P-82 补记：真实 `NOTICE_DATE` 滞后 +
37 个调仓日修前 18 / 修后 13 次命中），不假装有端到端收益对照。

**推广**：**收益类结论必须与产生它的环境一起存档**，否则环境一变就从「证据」退化成「传闻」。
判据：任何 A/B / 回测脚本在出结论前先自证关键前置数据非空
（板块映射 `size > 0`、行情行数 ≥ N、财报期数 ≥ N），空则**判红**而不是静默返回 None；
结果文件记录 `env_fingerprint`（数据文件存在性 + 版本 + 缓存命中数）。
「开关开了但没信号」和「开关压根没数据可用」在 `diff=0` 上是同一个假象。

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
| 白名单/枚举靠手写，加个字段就漏登记 | P-86 |
| 迁移门禁说「源码漂移」，但那是一次有意的行为修复 | P-87 |
| 门禁判红就改基线数字让它变绿 | P-86 / P-87 / G3-G4 |
| 同一个数字在计划/ADR/脚本里各写一遍 | P-88 |
| 取证链路读生产缓存或 datetime.now()，换个日子就假红 | P-89 / G1-G2 |
| 门禁说「全绿」，但 /ws、中间件根本没被扫描 | P-90 |
| 按函数名尾字分类，把「全目录遍历+压缩」判成轻量 IO | P-90 |
| 想给新端点加 offload，却被「消费者集合必须等于迁移集合」卡住 | P-91 |
| 扩了门禁视野，但取证重建只覆盖旧集合，新端点被静默改掉 | P-91 |
| 测试单独跑全绿、全量序列偶发红，还伴随某个用例突然变慢 | P-92 |
| monkeypatch 恢复了引用，但对象内部状态没恢复 | P-92 |
| 想复用策略结果省一次网络拉取，结果偷偷改了口径 | P-93 |
| 安全修复让阻塞调用变多，门禁判红，想直接放宽门禁 | P-94 |
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
| CLI/推送链运行时崩 `'dict' has no 'drop'`，测试却全绿（返回值形状漂移） | P-79 |
| 测试套件把 mock 假数据写进生产缓存，真实运行吃到假价格 | P-80 |
| sleep 型测试桩在 pytest 下瞬间返回假成功（conftest 全局 patch 了 time.sleep） | P-81 |
| 声明「无未来函数」却按 `end_date` 过滤财报 → 回测提前数月吃到年报 | P-82 / P-34 / P-36 |
| 拆包后合并旧分支，新模块落回 `lib/` 根且旧 import 全失效（冲突数却很少） | P-83 |
| 刚修完挂起熔断，新数据源又从 registry 之外卡死扫描 | P-84 / P-68 |
| 删掉本地行情/板块文件后，A/B 结论复算不出来且功能恒不触发（off/on 恒等） | P-85 / P-56 / P-59 |
| 财报「报告期」不等于「披露日」，按报告期裁 as_of 提前约 90 天逃顶 | P-82 |
| 公告日字段默认不返回 / `report_date` 其实是发布日期不是报告期 | P-82 字段对照段 |
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
2026-11-14 **合并决策审查（`feature/cycle-top-warning` → master，结论 NO-GO）**：
取证为 `git merge-tree --write-tree`（只读，77/30 提交、4 处冲突）+ 源码逐行核对，
全程未改工作树、未实跑测试。追加 P-82（财报按 `end_date` 裁 `as_of` 的语义层前视，
A/B 收益结论作废待重跑）、P-83（拆包后旧分支新增文件不被 rename detection 归位，
合并即 ImportError）、P-84（ADR-0019 刚收口的挂起缺口从新数据源原样复发，
缺「新增源必须登记」的机器判据），共 **84 条**。
同批修计数漂移的**根因而非补数字**：坑表头部「规模」行定为条数唯一真源，
`CLAUDE.md` / `RULES.md` 里硬编码的 80 / 61 改为引用该行，今后追加条目不再需要同步三处。
**同日补记 P-82 实证**：以分支真实源码 + 桩数据复算，前视数据使
`hot_count 3→0`、破位时 `exit_all→hold`；并对照现役 `as_of_date` 机器判据，
确认根因是「新增序列没接既有契约」而非「项目缺机制」。该条由制度推断升级为可复算证据，
仍按 append-only 追加、未改写上文。
2026-11-14 **A/B 复算尝试（结论：不可复算，分支判放弃移植）**：建独立 worktree
`D:/stock-scanner-ab-wt` 检出 `f340da0`（master 工作区全程未动），环境探测发现
`get_all_blocks()` 在 worktree 与 master 均为空 dict、`D:/TDX/vipdoc` 已随
`8025c95` 删除 ⇒ 顶部预警恒不触发，A/B 的 off/on 恒等（P-56 空判据复发）。
新增 **P-85**（共 85 个编号 / 81 条正文）；同时补记 P-82 的**接口实证**：
东财 `NOTICE_DATE` 字段真实存在，600519 近期 47 期年报中位滞后 90 天、
前视窗口占 49.8%，信号层 37 个调仓日修前 18 / 修后 13 次命中、
5 个调仓日的信号纯由未披露年报提前触发。
**未按原方案重跑端到端 A/B、未改分支代码**：前置依赖已不存在，补数据造出的
是生产环境并不存在的假环境，按「从根因修、不打补丁」止损。
2026-11-14 **分支关闭（用户拍板）**：`feature/cycle-top-warning` 本地分支删除、
审查 worktree `D:/stock-scanner-ab-wt` 移除；删除前打 annotated tag
`archive/cycle-top-warning-2026-11-14` → `f340da0`，`rev-list --count master..tag = 30`
确认未合并提交完整保留（该分支无远程副本，tag 仅本地、未推送）。
`TODO.md` 按 C6 追加 `[~]` 关闭条目并注明日期与四条原因，头部状态段同步索引。
本条为台账收口，不改写上文。
2026-11-14 **P-82 补记三源字段语义对照**（连接器实证）：东财 `NOTICE_DATE` /
NeoData `report_date`（须 `output_fields` 显式取，600519 `2025-FY` 滞后 107 天）/
Tushare `ann_date`（分支 `fields` 未取，本次未接口实证）。新增
「公告日默认不返回」「`report_date` 是发布日期不是报告期」两条判据与索引行；
规模仍为 85 个编号，未新增条目。

---

## 九、批次 A 安全加固追加（2026-11-14）

> 本章按 append-only 追加，不改写上文任何条目。规模由 85 → **87 个编号**。
> 权威记录见 `docs/adr/0020-batch-a-security-hardening.md`。

### P-86 · 手写白名单必然漂移：修「同步机制」而不是补两个字段

**现象**：`config.py` 的手写 `_CONFIG_SCHEMA` 只登记 4 个类 / 11 个字段，其中
`MLConfig.min_train_samples`、`CacheConfig.ttl_minutes` 在 dataclass 里根本不存在；
更致命的是 `_apply_overrides` 对「字段存在但未登记」仍无条件 `setattr`，于是
`{"factor":{"factor_weights":{"x":"abc"}}}` 可让 `sum(weights.values())` 抛 TypeError、
全站调用 `get_config()` 的端点 500；`get_config` 又返回 `PRESETS` 共享单例，污染持续到重启。

**根因**：不是「漏登记了哪两个字段」，而是**白名单靠人同步**。只要字段集合有两个真源
（dataclass 定义 + 手写字典），新增/改名/删除字段就必然漂移；注释写「未知字段跳过」
而代码写 `else: setattr` 也是同一类漂移。

**修复**：`_build_config_schema()` 由 dataclass 字段与类型标注生成白名单；敏感字段按
后缀自动排除（新增同类字段无需记得加黑名单）；`Dict[str, float]` 单列 `float_dict`
强制有限数值；范围约束是唯一手工登记项，由测试校验「键必须存在」；写入侧严格拒绝、
读取侧兼容跳过；写盘收口到 `write_config_overrides`（tmp + `os.replace`）；导出与导入
共用同一份白名单，保证 export→import 往返自洽；`get_config` 深拷贝切断共享单例。

**推广**：凡是「XX 集合」+「XX 白名单」两处手写的地方，都应把白名单改成从集合生成；
剩下必须手工登记的部分，要配一条「登记项必须存在于集合」的机器判据。

### P-87 · 把「源码逐字相同」当「行为相同」的代理，遇到有意修复必然误判

**现象**：批次 A 改了 `_compute_export_config`（导出白名单）与 `_compute_export_pdf`
（缓存键）后，L9 `offload_switch_diff.py` 判红：`migration export_config: statement
count 6 -> 5`、`migration export_pdf: statement #0 changed (Assign vs Assign)`。
L8 `other` 逐字段冻结也因 5 个端点的预期符号变化判红。

**根因**：L9 的设计目标是「证明 Phase 6 的 offload 搬迁没改业务行为」，它用「与
pre-commit 源码 AST 逐字等价」作代理。这在**搬迁期间**成立，但批次 A 是迁移完成
**之后**的有意行为修复——源码必须变，判据必然红。L8 的 `other` 冻结同理：它冻结
的是符号计数，代码一改就红，需要的是「重新取证」而不是「改数字」。

**修复（不是压红灯）**：
- L8 用脚本自带的 `--recapture --mode refactor --allow-endpoints ...`，按 G4 字段级
  取证：明确列出 5 个允许变化的端点，机器证明 **63 个未动端点逐字段一致**、
  blocking 只降不升，证据写入 `blocking-io-recapture-evidence.json` 第 8 条；
- L9 的 `export_pdf` 分歧通过「默认 preset 沿用历史缓存键」从设计上消除（下游消费者
  零改动）；`export_config` 沿用 ADR-0015 已有的 `MIGRATION_DIVERGENCES` 机制登记为
  第 3 项有意分歧 —— 该机制要求「仅校验接线完整性 + 行为正确性由专门测试覆盖」，
  本批新增 22 例行为契约测试正是配套，其中两条直接锁定 `export_config` 的新行为。
  严格冻结项 35→34，**其余 34 个迁移的判据强度没有放宽**。
- 判红时先做 `git stash -u` 对照实验：L3 双跑对拍 9 处判红在干净 HEAD 上**同样出现**，
  据此判定为取证环境漂移而非本批回归，按 G3 不改基线数字。

**推广**：门禁判红先问「判据在测什么代理」，再问「代理是否还成立」。给端点加豁免、
改断言值、改基线数字都是补丁；正确顺序是修判据或按取证机制重新取基线，并让新行为
有独立的机器判据兜住。

### P-88 · 同一个数字被手抄三遍，必然有一天对不上

**现象**：L10 `concurrency_probe.py:732` 写 `if len(paths) != 36`；而
`frontend-api-paths.json` 的 `counts.normalized_paths` 是 38，
`constraints.plan_stated_count` 是 18，ADR-0004 写「修正为 36」。
探针 CONSTANT、计划读数、实测清单三者各存一份，门禁每天判红
`frontend path count: 38 (expected 36)`。

**根因**：把「由源码扫描生成的集合大小」当成可以手工冻结的常量。
只要清单会随前端改动演进，任何抄在某处的固定数字都会过期。

**修复**：删掉硬编码，改为**调用 L7 的 `capture_frontend_paths()` 从 `static/` 重算，
再与登记清单做成员+数量双校验**；数量自动跟随，增删清单成员仍判红。
**刻意没有**直接改成 `len(paths) != fe["counts"]["normalized_paths"]`——
那是拿清单自己的字段校验清单自己，等于把判据删了（P-54 空判据）。
保留了 `counts.normalized_paths == len(paths)` 作为自洽校验（防只改 counts）。

**推广**：凡「集合 + 该集合的大小」同时被记录，大小必须能由集合重算出来，
不允许在第二个文件里再写一份；若判据需要期望值，期望值必须来自**独立重算**
或独立权威源，不能来自被校验对象本身。

### P-89 · 取证链路依赖运行时状态，换个环境/换个日子就假红

**现象**：批次 A 交付当晚 L3 双跑对拍 9 处 `scan.risk_check.*` 判红；
`git stash -u` 后在干净 HEAD 同样判红，一度被归因为「取证环境状态漂移」。
次日凌晨 L4 又冒出 `unified_modes: 26 field(s) differ`（交易数 6→5）。

**根因（两条，同构）**：
1. **L3**：`risk_checker.py:26` 是函数内延迟 import 的 `get_market_sentiment()`，
   它读 `get_realtime_quotes()` 的**生产缓存**。`dual_run_diff.py::_inject_shared_data`
   的 pin 名单漏了它（G2 / P-18 / P-19 复发）。压测脚本把非交易日行情写进
   `cache/realtime_realtime.parquet` 后，情绪比值 ≤ -0.30 → `BLOCK_NEW_RISK`
   → 9 个字段全变。基线采集时缓存不可用返回 `None`，门控落到中性分支。
2. **L4**：`BacktestRequest.start_date` 字段定义了却没透传给 `engine.run_backtest`
   （B2），`unified_modes` 链路靠 `datetime.now()-320d` 推窗口；跨到 11-15
   窗口平移一天，交易数 6→5、26 个 metrics 漂移。RULES §4.5 原写
   「指纹不含 equity 日期字段，故不判红」——**忘了 metrics/trades 本身随窗口变**。

**修复**：
- L3 按 G2 补 pin **定义方 `data_layer` + 使用方 `strategies` / `pipeline`** 三处，
  固定 `0.0`（与基线采集时的 `None` 降级态逐字段等价）；L4 补 `_run_simple`/
  `_run_full` 的 `start_date` 透传，并在取证链路显式传 `PIN_START_DATE`
  （`2025-12-22`，与 2026-11-07 基线采集窗口一致）⇒ **两条都恢复原基线，未重建**。
- 8 例机器判据：把生产情绪函数换成 `-1.0` 后取证链路仍须中性；`start_date`
  必须到达引擎；登记清单漂移 / counts 篡改必须判红。

**推广**：「在干净 HEAD 上也判红」只能证明**不是这次改动引入的**，不能证明
**根因在环境**。取证链路的正确性标准是「不依赖任何运行时状态」——
所有外部取数函数都要 pin（G2），所有窗口起点都要由显式常量推导并显式传参（G1）。
文档里写「已知边界：暂不判红」时，必须问一句「凭什么不判红」，并配一条能证伪的判据。

### P-90 · 门禁「全绿」只覆盖了它看得见的那部分路径

**现象**：L8 连续多批报「68 endpoints / 106 blocking / 全绿」，但上午审查实测
`/ws` 每 10s 同步做 6 次带全局锁的缓存读 + 全市场行情拉取 + `iterrows()`，
两个 `@app.middleware` 也在事件循环里写文件 —— **一个调用都没被计数**。
`backup_create` 对 `cache/` 做 `rglob('*')` + `ZIP_DEFLATED` 压缩，
却被 Tier B 登记成「轻量 IO，低于线程调度成本」。

**根因（三处结构性盲区，不是漏登记几个符号）**：
1. `ROUTE_METHODS` 不含 `websocket`，且 `_endpoint_decorators()` 硬过滤
   `path.startswith("/api/")` ⇒ `/ws`、根路由、静态辅助路由、中间件全不可见；
2. `_helper_index()` 只收 `ast.FunctionDef`（**同步**），而 `/ws` 调的
   `_get_ws_data` 是 `async def` ⇒ helper 展开直接跳过整条阻塞链；
3. Tier B `_tier_b_group()` 只按 `callee.rsplit(".",1)[-1]` 单个尾名查表，
   `rglob`/`ZipFile`/`extractall` 都躺在 `LIGHT_IO_TAILS` 里 ⇒
   「遍历整个目录 + CPU 压缩」和「读一个小 JSON」被归成同一类。

**修复（B1，先修判据、不动业务代码）**：
- 端点发现拆成**两个互不干扰的集合**：`endpoints`（68 个 HTTP `/api/`，
  成员+数量继续与 L7 对齐）与 `extended_endpoints`（6 个：`/ws`、3 个非 `/api/`
  路由、2 个中间件）。扩展集合独立做成员+数量 / blocking 单调 / `other` 冻结，
  因此扩大视野**完全不动** L7/L9/L10 的端点集合 —— 这是能安全扩展的关键；
- `_helper_index` 同时索引 `AsyncFunctionDef`，并排除带路由装饰器的端点函数
  （否则端点之间会互相串调用）；实测 `/ws` 立刻采到 **10 个阻塞调用**；
- Tier B 改**符号组合语义**判定：同一端点内 `rglob|glob` 与
  `ZipFile|extractall|namelist|write` 同时出现 ⇒ `compute_pending`，
  且组合规则优先于单名 override（防 `zf.write -> light_io` 把规则稀释掉）；
- `--selftest` 新增「`/ws` 加阻塞调用必判红」「删扩展端点必判红」
  「目录遍历+压缩不得判 light_io」；`--recapture --mode scope` 提供范围扩展的
  G4 取证（既有 HTTP 端点逐字段不变、新增扩展端点须显式列出）。

**取证**：G4 重建 `0 changed / 68 untouched identical`，blocking 106→106、
offloaded 38→38，Tier B 102 条 / 0 未分类 / Tier A 候选 1→3。

**推广**：门禁输出「N endpoints 全绿」时，先问「这 N 个是怎么枚举出来的」。
枚举规则本身若带过滤器（前缀、装饰器类型、sync/async、目录范围），
「全绿」就只对该子集成立。扩视野时把新端点放**独立集合**并沿用同等判据强度，
比把它塞进原集合再改所有下游基线安全得多。按函数名尾字做语义分类同理：
分类要看**调用组合**，不要看单个名字。

### P-91 · 视野扩了，取证范围没跟上；以及「集合相等」判据挡住后续治理

**现象（B2，两条同批暴露）**：

1. B1 把 `/ws` 和中间件纳入 L8 的 `extended_endpoints` 后，`B2` 改 WS 实现时
   发现 `--recapture` 的 G4 取证**只遍历 `old["endpoints"]`**（HTTP 68 个）。
   也就是说：extended 端点的任何变化都会在重建基线时被**静默吞掉**，
   「其余端点逐字段一致」的承诺对新集合根本不成立。
2. 想给 WS 后台任务 / `backup_create` 加 `run_blocking` 时，L9
   `check_consumer_registry()` 要求 `OFFLOAD_CONSUMERS` 严格等于 37 个
   `MIGRATIONS`（`registered != covered` 即判红）⇒ 新增灰度消费者会连
   迁移对拍结构一起判红。

**根因**：两处都是**把「当时的状态」写成了「集合相等」约束**。
- 取证工具假设「端点集合 == HTTP /api/ 端点」，视野扩展后这个等式不再成立；
- L9 假设「所有 offload 消费者都来自 Phase 6 的 37 次迁移」，治理继续推进后
  这个等式也必然失效。集合相等在「一次性收官」时很好用，在「持续治理」时
  会变成挡路的门。

**修复（本批不用补丁绕过）**：
- `recapture()` 改为 `_all_records()` 统一收集 `endpoints + extended_endpoints`，
  主循环用 `e["key"]` 字符串做键，`allowed` 同步转字符串集合 ⇒
  **两个集合都享受同一套 G4 字段级取证**。B2 实测证据第 10 条
  `2 changed / 72 untouched identical`（74 个端点里 2 个显式允许变化）。
- 不硬塞 consumer，改用**两条更根因的修法**：
  * WS：N 个连接各自 sleep+构造 ⇒ 单个后台任务构造一次 + `broadcast()`，
    约 100 倍降幅；端点**保留一次** `_get_ws_data()` 调用，
    使 B1 刚修好的 helper 展开视野不退化（否则盲区又回来）。
  * backup：白名单打包排除 `cache/`（消除「为什么要遍历压缩整个缓存」
    这个前提，顺带闭合 P1-8 的信息泄露）+ 压缩体抽同步函数走
    `asyncio.to_thread`（L8 已登记的 `OFFLOADED_TAILS`，无需新 consumer）。
- 限流同理删掉 `data_layer` 第二套 `_RateLimiter`，收敛到
  `lib.data.cache.rate_limiter` 单一实例并按数据源分桶。

**遗留（明确登记，不算完成）**：若要让 WS/backup 正式纳入灰度开关，
必须单独 ADR 把 L9 的「迁移证据集合」与「活跃消费者集合」拆开，
不能偷偷往 `OFFLOAD_CONSUMERS` 里塞名字。

**推广**：每次扩展判据的**发现范围**时，同步检查所有「遍历旧集合」的下游工具
（取证、登记、报告、自证），否则视野扩了、审计却没跟上，等于开了一扇没锁的门。
判据里写「A 集合 == B 集合」时，先问「A 和 B 会不会各自独立演进」；
会，就该改成「A ⊆ B + 每个 B 都有证据」这类可演进的关系。

### P-92 · 全量序列偶发红 + 某个用例突然变慢，先做对照实验再动代码

**现象**：B3 改完三项 `lib/` 内优化后跑全量，`test_source_call_timeout.py::
test_multi_source_loop_falls_through_hang_to_next_source` 报
`AllDataSourcesFailedError: ['baostock: empty/invalid', 'akshare: empty/invalid']`，
同时 `test_batch_quick_score_basic_parallel` 从正常的秒级涨到 **104s**，
全量耗时 27s → 121s → 181s。**单独跑该文件：4 passed / 3.4s，全绿。**

**错误的第一反应**（会踩的坑）：以为是刚改的限流/锁/单例引入的性能回归，
开始逐项回退业务代码，或者把那个失败用例的断言放宽让它变绿。

**定因方法（做对照，不猜）**：
1. `git stash push -u -- <本批 4 个源文件>` 撤回全部改动后跑**同一个慢测试**
   → 35.62s（改动在时 37.27s）⇒ 慢不是本批引入；
2. 撤回后再跑**全量序列**，日志里同样出现「所有数据源均无法获取」
   ⇒ 失败也不是本批引入；
3. 由此把注意力从「改了什么代码」转到「测试之间共享了什么状态」。

**根因**：`lib/data/data_source_registry._CIRCUIT_BREAKERS` 是模块级全局字典，
里面的 `CircuitBreaker` 对象自带 `failure_count` / `state`。前几个用例用
`monkeypatch.setitem(..., "baostock", breaker)` 替换键，测试结束后
monkeypatch **只把键恢复成原来的对象**，并不重置对象内部累积的失败计数。
全量序列里前面的用例已经对那个原对象记过失败 ⇒ 后续用例拿到的 breaker
可能已是 `open` ⇒ 所有源 fail-fast ⇒ 全源 empty/invalid。
`test_batch_quick_score` 变慢是同一状态引起的连锁降级（真实取数走重试/退避），
不是锁竞争。

**修复（修隔离，不改断言）**：在该测试文件加 autouse fixture，
每个用例前把 `_CIRCUIT_BREAKERS` 整体换成**全新实例字典**，
让「用例自身行为」成为唯一变量。4 例断言一个字没改。

**推广**：
- 「单独跑绿、全量红」几乎总是**共享状态 / 顺序依赖**，不是业务代码回归；
  先做 `git stash` 对照实验定性，别急着回退代码。
- `monkeypatch.setitem/setattr` 恢复的是**引用**，不是对象内部状态。
  对任何自带计数的全局对象（熔断器、单例、缓存、限流器），
  测试都要显式重置或整体替换实例。
- 全量耗时突然翻倍时，先看 `--durations`，慢点往往是被降级路径拖住的
  **另一个**测试，而不是报错那个。

### P-93 · 复用「策略结果」省一次网络拉取，结果偷偷改了口径

**现象**：`run_all_strategies` 里「热门板块」策略在并行 executor 中跑一次，
「主线板块过滤」阶段又完整调用 `get_hot_board_candidates(config)` 一次
（P2-3，含涨停池网络拉取）。最直观的修法是让下游直接读 `results["热门板块"]`，
省掉第二次调用。

**根因**：`results` 装的是**加工后对外结果**，不是策略原始输出。中间隔着
① `symbols` 白名单过滤（候选股被裁掉）、② `apply_strategy_weights_to_candidates`
列/值调整、③ 策略被 `_failure_tracker` 降频跳过时该键根本不存在。
直接复用会让主线板块过滤拿到一份**被裁过、被改过、甚至为空**的板块清单，
静默改变选股结果 —— 这是数值口径变更，不是性能优化。

**修复（B4）**：在并行结果组装的**变换之前**留一份原始副本
`raw_hot_boards = df.copy()`，下游优先复用这份；策略被降频/执行失败/返回空时
`raw_hot_boards is None`，**回退到原路径**再调一次，行为不变。
顺带把 `hot_boards.iterrows()` 里的板块名做 set 去重（`hot_sector_codes` 本来
就是 set，重复查询不改变任何数值，只是少建几次连接）。
验收：L3 双跑对拍 3 chains / L4 回测 5 chains / L5 评分 9 sources 全 PASS。

**推广**：
- 复用中间结果前先列一遍「这份数据从产出到被读之间经过哪些变换」，
  在**第一处变换之前**取快照；拿不到就别复用，保留回退路径。
- 「省一次调用」类优化必须用数值口径门禁验收（L3/L4/L5），
  不能只靠函数级单测——调用次数少了、结果变了，测试也可能全绿。
- 降频/熔断/重试这类**可选执行**路径会让中间结果缺失，
  复用逻辑必须显式处理 `None`，否则会静默退化成空输入。

### P-94 · 安全修复让阻塞调用变多，第一反应不该是放宽门禁

**现象**：`backup_restore` 原来一行 `zf.extractall(extract_dir)`；为修 Zip Slip
（P1-7）改成逐成员 `is_relative_to` 校验 + `zf.open` + `open` + `mkdir` 写入。
L8 立刻判红 5 条 `blocking INCREASED`（`is_dir` / `mkdir`×2 / `open` / `zf.open`），
外加 3 条 `UNREGISTERED blocking symbol`。

**错误的第一反应**（两条都是补丁）：
1. 把 `POST /api/backup/restore` 塞进 `--recapture` 的 allowed 名单，
   让「blocking 只准降」对它失效；
2. 或者直接往 `BLOCKING_TAILS` 里补登记，把红灯压成绿。

**正确的解法**：安全修复和性能门禁在这里**是同向的**——逐成员校验必然要开文件、
建目录，那这段本来就应该在线程池里跑。把解压体抽成同步函数
`_compute_backup_restore()`，端点 `await asyncio.to_thread(...)`：
L8 记 `blocking` 归零、`offloaded` +1，判据方向完全一致，**不需要放宽任何门禁**。
实测 G4 取证 `3 changed / 71 untouched identical`，blocking 97→94、offloaded 39→40。

**推广**：
- 门禁判红先问「这个变化是判据错了，还是实现方式错了」。安全加固引入的
  文件/网络操作，多数属于后者——它本来就该 offload。
- 修安全问题时**不要**顺手放宽性能判据：那会让「blocking 只准降」这条
  最有力的约束变成可协商的。
- 如果确实必须增加阻塞调用（例如同步密码学运算），应该走
  `--recapture --mode refactor` 并在 evidence 里逐条写明理由，
  而不是把端点从判据里摘掉。

### P-95 · 审查报告的结论也要验机制：多 worker 不是「重复推送」是「状态分片」

**现象**：安全审查报告 2026-11-14 写「`uvicorn --workers N` 下每个 worker 各起一个
广播任务，造成重复推送」，并据此把「多 worker 广播收口」列为遗留。

**为什么是错的**：`manager.active_connections` 是**进程内内存**。多 worker 下
一条 TCP 连接只被 OS 分发到一个 worker，那个 worker 的 `broadcast()` 只遍历
连到自己的连接——单客户端不可能收到两份推送。照错误前提去做「广播去重」
（进程间单飞、共享连接表）纯属白干，是典型的补丁加补丁。

**真实风险**（追到状态载体才找得到）：
- `_ws_broadcast_loop` 每 worker 各跑一份 → 数据源被拉 N 倍；
- `scan_task_registry._scan_tasks` / `_inflight_scan` 与 `api_core._cache_*`
  都是进程内状态 → B4 的「同 preset 单飞」在多 worker 下形同虚设，
  同一 preset 可被并发扫描、缓存互相覆盖；
- 客户端落到不同 worker 会读到不同快照。

**正确做法**：先验证结论的机制是否成立，再决定改什么。本批加启动期 fail-fast
守卫（`lib/infra/worker_guard.py`）：默认拒绝多 worker，显式 opt-in 才放行并告警；
真正根治（状态外置到 Redis / 独立进程）是 L9 契约拆分后的架构决策。

**推广**：
- 审查报告是**线索**不是**事实**。写进修复方案前至少追一层到代码里的状态载体。
- 「每个进程各起一个任务」不等于「结果重复」——先看结果是按连接分发还是全局共享。
- 守卫的错误信息必须给**可直接粘贴的修复命令**，不能只说「不支持多 worker」。

### P-96 · 「冻结清单」归属也要验：报告说不能改的端点，可能压根不在清单里

**现象**：审查报告 P2-10 把 CSV 公式注入列为遗留，理由是
「`export_positions` / `export_trades` 属 L9 严格冻结的 34 个迁移端点，放宽不划算」。

**核实**：`MIGRATIONS` 里只有 `export_trades` / `export_trades_excel` /
`export_config` / `export_pdf` 四个 export 消费者。`export_positions` 与
`export_positions_excel` 没有 `_compute_*` 函数、逻辑全在端点体内，**根本不在冻结清单**。
整条「两个端点都不能改」的前提只对了一半，遗留因此多躺了两个批次。

**顺带纠正**：L9 的 PASS 文案长期写「34 严格 + 2 分歧（RF-02）」，实测是
**34 + 3**（`export_config` 因批次 A 的密钥白名单修复早已登记分歧），文档口径漂移。

**正确做法**：引用「冻结 / 不动 / 受保护」作为不改的理由前，
先 `grep` 一次被冻结对象的**实际清单**，别信二级摘要（报告、TODO、ADR 转述）。
真在清单里的，用既有 `MIGRATION_DIVERGENCES` 显式登记 + 行为判据，
不是放弃修复，也不是放宽逐字等价门禁（ADR-0021）。

### P-97 · 单飞判据必须覆盖「冷启动竞态」和「真实进程」，不能只测稳态

**现象**：多 worker 广播单飞首版实现，8 个判据全绿，但真实 `uvicorn --workers 2`
启动时报 `[Errno 13] Permission denied`，且 8 进程集成判据显示**全员都构造了数据**。

**两个盲点**：
1. **冷启动无快照**：没抢到锁的 worker 立即读快照，但锁持有者还没写完 →
   `read_snapshot` 返回 None → 全员 fallback `compute()`。单测只测「已有快照」
   的稳态路径，完全看不到这个窗口。
2. **Windows 并发文件 IO**：单测用 `tmp_path`（预建目录 + 单进程），
   真实双 worker 同时 `mkdir` / `create` 才触发 PermissionError。

**正确做法**：
- 单飞类代码必须有「N 个真实进程同时首次进入」的集成判据，断言**构造次数
  恰好 1**，而不是「结果一致」就够了（结果一致可能是大家都算了同一份）。
- 文件锁 / 原子写路径要带短重试 + 每进程独立 tmp 文件名；不要假设目录已存在。
- **固定等待时间不是解法**：第二版改成等 1s 再读快照，单跑判据过、全量序列
  下仍偶发 4 进程 compute（spawn 慢导致超时点聚集）。最终改成「等待期反复
  重抢锁，拿到锁后先读快照再决定是否构造」，从机制上消除同时超时的竞态。
  判据要**连跑 3 次**才敢收工，跑一次不算。
- 守卫这类「部署形态」逻辑，要单独验一遍命令行路径——只测环境变量会被
  `--workers N` 一行命令绕过（本批 P0 漏洞，实测两个 worker 都起来了）。
