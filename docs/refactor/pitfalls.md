# RF-01 踩坑集（工程失败模式与推广）

> **来源**：2026-11-07 RF-01 分阶段重构（Phase 0 → Phase 3）的真实执行过程。
> **范围**：只收录**本项目实际踩到过**的坑，每条都带可复算证据；不含"可能会踩"的泛泛而谈。
> **规模**：**125 个编号（P-01～P-125）**，分十二类；其中 121 条有独立正文条目，
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
| 「源不可用」但换个时间又通了 / 东财一个接口死一个活 | P-99 |
| 单文件跑绿、全量跑红（顺序依赖） | P-100 |
| 数据恢复后主流程崩在 `if dict.get("data")` | P-101 |
| 每次运行都「自动归一化」权重 | P-102 |
| 候选股综合评分出现 NaN | P-103 |
| 每只股票都报「找不到 gpcw 文件」 | P-104 |
| 策略日志报 NameError 但扫描仍 exit 0 | P-105 |
| 修完 bug 后整个扫描从 2 分钟变成 10 分钟超时 | P-106 |
| 单次打分 13.8s，profile 里同一指数接口拉两次 | P-107 |
| 策略日志报 NameError 但扫描仍 exit 0 | P-105 |
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
| 测试全量红、单跑绿，红/绿还随文件组合与时序变化 | P-119 |
| 判据测试「昨天绿今天红」，代码和上游都没改 | P-120 |
| 「净额几乎全 NaN」但原始 API 帧里明明有值 | P-121 |
| 科创板量价因子静默错 100 倍，链路照跑不报错 | P-122 |
| 本地仓一半文件日期降序，`tail()` 取到的是最老行 | P-123 |
| 同一数据集 5 种列集并存，厂商原始帧直落仓 | P-124 |

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
唯一入口；3 个 Tier A 回测端点 offload（固定 commit `385e449` AST 逐语句等价）；
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
2026-11-20 **文档分层重构**：新增 P-115（文档分层靠人自觉必然回漂 ⇒ 机器判据
把「单一真源」变成可执行；判据首跑 101 项须先分类再修），并新增 `AGENTS.md` 作为
AI 行为唯一真源，共 **115 条**。
2026-11-20 **资金流 P0 修复**：追加 P-114（缺失被 `fillna(0)` 伪装成「真实零值」，
以假中性 1.0 分进入四维加权；叠加 int64 代码列前导零丢失致 0 开头股票批量层
100% MISSING），共 **114 条**。本批 L1 **1713 passed / 12 skipped / 5 failed**
（5 failed 经 stash 同口径对照实验证实为 HEAD 既有，非本批引入），L0–L10 全 PASS。
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

### P-98 · 「没有数据」往往是「被钉死在死源上」——先 grep 取数入口

**现象**：用户问「项目里数据够吗」，表面看 cache 空、回测结果空、TDX 目录不存在，
像是「没有数据源」。逐源实测后发现 **baostock 完全可用**（59 行真实日线），
真正的问题是回测引擎有 7 处硬编码 `source="tdx_local_qfq"`——指向用户已删除的
本地 TDX 目录，回测永远走那条死路。

第二层：修完硬编码后回测仍 `KeyError: 'close'` 崩溃——指数基准
`get_index_hist` 只有「本地 TDX → pytdx 网络」两条路，都不可达时返回空，
而调用方没有空守卫。给指数加 baostock 兜底后才真正跑通（8 股 500 天产生交易）。

**推广**：
- 「取不到数据」先逐源探测 `_fetch_from_source(code, days, src)`，
  确认是「源都挂了」还是「代码被钉死在某个源」。两者修法完全不同。
- 多源自动切换的代码里，任何 `source="xxx"` 硬编码都是可疑的——
  它绕过了整个兜底链。用静态守卫禁止（判据 `test_no_hardcoded_dead_tdx_source_in_backtest`）。
- 指数代码的市场归属和个股不同：`detect_market("000300")` 返回 `sz`，
  但沪深300指数是 `sh.000300`。指数映射必须单独写，不能复用个股的。
- 验证要**真跑一次**：L1 全绿但回测 0 交易，靠测试发现不了——
  mock 把死源问题完全掩盖了。

### P-99 · 「源不可用」要先分清是瞬时网络、单个 host，还是整个源

**现象**：2026-11-18 上一批结论写着「全市场实时行情仍不可得：akshare spot 外网失败、
pytdx WinError 10057、baostock query_all_stock 返回 0 行」。当晚复测：同一个
`get_realtime_quotes(force=True)` **返回 5579 行 / 356 只北交所**，
`ak.stock_zh_a_spot()` 12.3s 正常返回。

**根因分四层，每层都值得单独判据**：

1. **瞬时网络 ≠ 源不可用**。当时代理在 502（同批日志里 push 也被 502 阻断）。
   把「那一次失败」写成「源不可用」会让后续批次在错前提上做设计
   （例如去接一个根本不需要的新数据源）。
   判据：结论落文档前必须贴「时间 + 命令 + 返回行数」，不能只写结论。
2. **东财是「按 host 部分可用」，不是整个源**。`push2`/`push2his` 空响应或连接重置，
   而 `push2ex`（涨停池 41 行 / 龙虎榜 188 行）、`emweb`（F10 财务 48KB）正常。
   判据：`scripts/host_health_probe.py`（逐 host + 业务标记，不看 HTTP 200）。
   熔断也要按 host 计（ADR-0025），否则最差的 host 决定整个源的命运。
3. **同功能多套实现 = 真正的维护成本**。实时行情曾有 4 个同名
   `get_realtime_quotes`（`data_layer` / `data_api` 门面 / `data_ashare` / `data_adata`），
   其中 `data_ashare` 版只跑前 100 只、`data_adata` 版无引用；腾讯解析另有三份。
   判据：AST 静态守卫「`lib/data` 下谁定义了该函数」+ `.split("~")` 唯一性。
   **这个守卫上线当天就抓到了第三份解析**（`data_sector` 的 `q=s_` 变体，死代码）。
4. **跨源口径必须有契约，否则静默错**：
   - 单位：腾讯「手 / 万元」vs 新浪「股 / 元」→ 资金流 `vol_ratio` 差 100 倍，
     分支永不生效且不报错；
   - 代码形态：新浪 `sh600519` vs 内部 6 位 → `run.py` 取股票名、`steps.py` 取实时行
     全部静默落空（实测 0 行命中）。
   判据：固定报文钉单位换算 + 归一后 6 位精确匹配可命中。

**推广**：
- 数据源故障的排查顺序是 **host → 接口 → 参数 → 源**，不是「源挂了」一步到位。
- 任何「多个同名函数」的目录都要有一条静态守卫钉住「只允许一个真实现 + 门面」。
- 「降级」必须显式：阈值告警 + 不写缓存。28 只样本帧写进 30 分钟缓存，
  等于让整轮扫描在错的分母上算涨跌比。

**第三次复发（2026-11-19 00:15）**：个股日线 8 个已注册源全 0（baostock `10001011` 黑名单 / push2his 不可达 / tushare 无权限 /
adata·westock 缺 Node / TDX 已删），看起来「没数据了」；实际新浪与腾讯的**未注册**网页网关都返到最近交易日（6/6 标的，含北交所与指数）。
教训收敛成一句：**故障排查要先问「我探测的是全部通道，还是只有我注册过的通道」**；新增源后要用跨市场段的 6 标的日期判据钉住（`scripts/kline_coverage_probe.py`）。
**第四次（同日 23:10）**：腾讯两台 fqkline 主机在数小时内**交替**被 WAF 拦
（web 拦→ifzq 正常→ifzq 拦→web 正常），且换浏览器 UA 无效——是主机维度策略。
把主机写死成任一单台都是追逐外部状态；正解是双主机轮换 + 按 host 分别计数健康
（ADR-0032）。推广：**当某个外部依赖在不同入口间轮换故障时，源栈要登记全部入口并逐台尝试**，
不要用「上次管用的那个」当设计不变式。

### P-100 · 进程级单例按用例清零——否则测试长出顺序依赖

**现象**：全量跑 1533 例出现 2 红（`tests/test_data_tdx.py` 的两个 akshare 兜底用例），
而单跑该文件 5/5 全绿。红的表现是「akshare mock 明明有数据，却拿到空 DataFrame」。

**根因**：`lib/data/host_health.py` 是**进程级单例**（生产上正确：跨请求共享探测结果，
避免每个请求都重新发现 host 不可用）。但前序某个用例模拟了「push2his 连续失败 3 次」，
把该 host 置为 `open`；后续用例走 `call_with_health` 时被短路成空帧，**mock 根本没被调用**。
状态跨用例泄漏 ⇒ 用例顺序决定成败。

**修法**：修在测试基础设施，不修在业务代码，也不改「受害用例」。

```python
# tests/conftest.py（autouse）
@pytest.fixture(autouse=True)
def _reset_host_health_between_tests():
    from lib.data.host_health import reset_host_health
    reset_host_health(); yield; reset_host_health()
```

三条判据（`tests/test_host_health.py`）：

1. **注册判据**：`"_reset_host_health_between_tests" in request.fixturenames`
   —— 光写函数不挂 autouse 等于没写；
2. **端到端判据**：子进程跑两条有序用例（A 三次失败把 push2his 打开，B 断言它可用），
   要求 `2 passed` —— 隔离失效时 B 必红，判据本身可被证伪；
3. 边界：清零只在测试进程内发生，生产代码里没有任何「测试开关」。

**推广**：
- 任何进程级单例都要先回答「测试间怎么清零」：断路器、host 健康、in-flight 注册表、
  限流器、单例缓存，逐个都要有答案；没有答案就会长出顺序依赖。
- 顺序依赖的典型症状：**单文件绿、全量红**；或加 `-p no:randomly` 后时红时绿。
  先用「单跑 vs 全量」二分，不要先去怀疑被测逻辑。
- 别用「给生产代码加重置开关」解决测试隔离——那会让业务代码知道测试的存在，
  且开关本身又会变成新的全局状态。


### P-101 · 同一份数据的两种形状：只有数据分支被激活才暴露的契约分裂

**现象**：2026-11-19 日线源恢复后第一次真实盘后扫描崩在
`pipeline.py:905` —— `ValueError: The truth value of a DataFrame is ambiguous`。
此前扫描走不到 ML 分支（个股日线全 0 → 无候选），所以问题潜伏。

**根因**：`_enrich_with_ml_scores` 返回 `"data": result_df.head(10)`（DataFrame），
但三个消费方全部按 `list[dict]` 使用：

| 消费方 | 用法 | DataFrame 下的结果 |
|:--|:--|:--|
| `pipeline._apply_regime_adjustment` | `for c in candidates: c.copy()/c.get()` | 迭代出列名 → AttributeError |
| `pipeline.run_all_strategies` | `if ml_ranking.get("data")` | 布尔判断直接抛 ValueError |
| `reporter.report_generator` | `for item in data[:10]` | item 是列名字符串 |

同一个函数还返回 `top10`（已经是 `to_dict("records")`）——一份内容两种形状。

**修法（根因，不是调用处特判）**：`"data"` 与 `"top10"` 共用同一份 `list[dict]`。
不在 `if` 处加 `isinstance(df, pd.DataFrame)` 分支——那只会让契约继续分裂。

**推广**：
- 被长期跳过的功能分支是**契约盲区**：数据一恢复，潜藏的类型不匹配会集中暴露。
  修完数据源必须真跑主流程，不能只跑判据。
- `DataFrame` 不能做布尔判断、不可直接 JSON 序列化；跨模块传递的候选列表
  统一用 `list[dict]`（这也是项目其它 scan_result 字段的口径）。
- 同一函数里出现 `data` 与 `top10` 两个表示同一集合的字段，是形状漂移的早期信号。
  判据：`tests/test_ml_ranking_contract.py`（形状 / JSON / 消费方联动 5 例）。


### P-102 · 运行期归一化不是修复，错在声明处

**现象**：真实盘后扫描每次刷 6 条
`因子权重和不为 1.0（当前 1.0020），自动归一化`。

**根因**：`FactorConfig.strategy_weights` 六个 `0.167` = **1.002**；
`conservative.factor_weights` 声明和只有 **0.40**。
`get_config()` 与 `weighted_signal_combination()` 各自在运行期归一化——
警告被当成常态，声明始终是错的。外部直接读 `PRESETS` 的代码拿到未归一值，
而走 `get_config()` 的代码拿到归一值，**同一个配置两种语义**。

**修法**：定义处 `_sum_one()`，让所有内置预设声明即和为 1.0；
运行期兜底保留，仅服务 JSON 热覆盖等外部输入。

**判据**：`tests/test_config_weight_contract.py` 8 例——三个预设的声明和、
`get_config()` 不再触发归一化警告、factors 默认权重、`JSON 坏权重仍被兜底`。

**验证**：真实扫描警告数 6 → **0**，候选股 23 → 23；L3/L4/L5 数值基线零漂移
（证明修的是声明，不是行为）。

**推广**：
- 「自动修正」日志反复出现 = 声明与运行口径分裂。要么修声明，要么把
  「归一化」明确写进契约（例如定义为 score 权重而非概率），不能两者都不是。
- 负权重预设尤其要查：conservative 声明和 0.40 靠运行期 ÷0.4 才变成 1.0，
  相对比例不变但绝对幅度全靠兜底，读 PRESETS 的调用方会拿到完全不同的量级。


### P-103 · 两份成员不同的列表不能按位置对齐

**现象**：盘后扫描的龙虎榜候选里 `*ST宜康`、`*ST民控` 的 `综合评分 = NaN`。

**根因不是评分异常**：`get_lhb_candidates` / `get_zt_candidates` 先构造
`rt_rows = [row for code in codes if row_map.get(code)]`（只含有实时行情的代码），
却用 `scores = [score_results.get(code, {}).get("score") for code in codes]`
遍历**全部**候选代码。没有实时行情的 *ST / 停牌股取到 `None`，
pandas 写列时变成 NaN。NaN 参与 Top-N 排序会破坏顺序且不报错。

**修法**：抽 `_attach_quick_scores()`，按**代码**写回；两类缺失都给确定值：

* 无实时行情 → `综合评分=0.0`、`评分状态=无实时行情`（信号保留，排序沉底）；
* 有行情但评分为 None/NaN/非有限数 → `0.0`、`评分状态=评分失败`。

**判据**：`tests/test_score_alignment.py` 4 例（缺失/失败/真实策略链/静态守卫）。

**推广**：
- 看到 `for x in A` 构造、却 `for x in B` 消费的列表，先怀疑成员集合是否相等；
  不等就应该用 dict 按 key 取值，不能靠位置对齐。
- NaN 不该静默进入排序字段：要么显式标记+中性值，要么过滤，不能「先放着」。
- 这类缺陷只在**特定标的缺席某张源表**时触发（*ST、停牌、新股），
  常规样本测试很难覆盖——判据要专门构造「候选有、行情无」的场景。


### P-104 · 源可用性要在源层判一次，不是每个调用点告警一次

**现象**：每次盘后扫描刷数十条 `找不到 gpcw*.dat 文件`。
TDX 本地目录早在 ADR-0017 就已删除，财务适配器却对**每只候选股**
调用 `GpcwReader`，读不到就告警，然后才走 baostock 兜底。

**根因（两层）**：

1. `TDX_FIN_PATH` 硬编码 `D:/TDX/vipdoc/cw`，不认 `TDX_DATA_PATH`——
   用户按 ADR-0017 恢复本地 TDX 目录后，财务数据仍然找不到；
2. 「源不可用」的判定被放在每个调用点（读取器内部），而不是源选择层。

**修法**：

* `TDX_FIN_PATH = Path(os.environ.get("TDX_DATA_PATH", "D:/TDX/vipdoc")) / "cw"`；
* 在 `data_tdx_fin` 中定义**唯一** Availability 判据 `tdx_fin_available()`；
* 适配层 `get_stock_fundamentals()` 与 `data_layer.get_stock_financial()` /
  `get_financial_codes()` 三处入口共用它，不可用直接走兜底；
  GpcwReader 保留显式调用时的告警（不是压掉日志，是消除无效调用）。

**首版漏判**：只改了适配层，真跑仍有 13 条告警。栈追踪显示实际调用来自
`factors.py:1105 → data_layer.get_stock_financial → GpcwReader` ——
**「修完一个入口就以为修完」是同型缺陷的复发**。教训：先在真跑里抓调用栈，
列出全部入口，再一次性修完；判据也要覆盖每个入口（现为 5 例）。

**判据**：`tests/test_tdx_fin_availability.py` 4 例（路径 env 覆盖、不可用跳过且不告警、
可用时仍走 TDX、读取器本身未被删除）。

**推广**：和 P-99/P-103 同族——**判定放错层级**会同时制造噪音和错口径。
凡是「某源在这个环境必然不可用」，都应在源选择层短路一次，
不要在每个标的的调用里重试+告警。


### P-105 · 策略层 `except → log → empty` 让 NameError 静默少一个策略

**现象**：真实扫描 exit 0、23 只候选，但日志里有一条
`[ERROR] 量价突破策略出错: name 'date' is not defined`——该策略本轮产出 0 候选，
却没有任何门禁判红。

**根因**：P1-3 给 `get_volume_breakout_candidates` 加 `as_of_date` 参数时，
`ScoreCard.score(..., as_of_date=date)` 的调用点没跟着改。`date` 未定义，
一进入打分分支就抛 NameError，被函数末尾的
`except Exception: logger.error(...); return pd.DataFrame()` 吞掉。

**修法**：`as_of_date=as_of_date`（全仓只此一处，grep 确认）。

**判据**：`tests/test_strategy_nameerror_guard.py` 2 例——
静态守卫「全仓不得再有 `as_of_date=date`」+ 行为判据「量价突破分支必须走到
打分卡且 as_of_date 原样透传」。

**推广**：`except → 记日志 → 返回空` 是**静默降级**，不是错误处理。
只要被测分支没被 L1 覆盖，这类缺陷就能在全绿状态下长期存活。
对策：① 策略函数的每条主分支都要有「能走到关键调用点」的行为判据；
② 真跑日志里任何 `[ERROR]` 都必须逐条解释，不能因为 exit 0 就放过。


### P-105 · 策略层 `except → log → empty` 让 NameError 静默少一个策略

**现象**：真实扫描 exit 0、23 只候选，但日志里有
`[ERROR] 量价突破策略出错: name 'date' is not defined`——该策略本轮产出 0 候选，
门禁没红。

**根因**：P1-3 给 `get_volume_breakout_candidates` 加 `as_of_date` 参数时，
调用点仍写 `ScoreCard.score(..., as_of_date=date)`；`date` 未定义，
一进打分分支就抛 NameError，被函数末尾的 `except Exception: logger.error(...);
return pd.DataFrame()` 吞掉。全仓仅此一处。

**修法**：`as_of_date=as_of_date`；判据 `tests/test_strategy_nameerror_guard.py`
2 例（静态守卫 + 行为判据「分支必须走到打分卡且参数透传」）。

**推广**：`except → 记日志 → 返回空` 是静默降级，不是错误处理。
只要 L1 没覆盖该分支，缺陷就能在全绿状态下长期存活。
真跑日志里任何 `[ERROR]` 都要逐条解释，不能因为 exit 0 就放过。

### P-106 · 修好分支后才暴露的无超时第三方 socket 调用

**现象**：修完 P-105 后，真实扫描从 1m46s 变成 **10 分钟超时（exit 124）**。
之前不是"快"，是量价突破策略在打分前就崩了，根本没走到慢调用。

**根因**：`faulthandler.dump_traceback_later` 抓到卡点：
`ScoreCard.score → layer2_sector_strength → _get_industry →
baostock.query_stock_industry → socketutil.send_msg`。baostock 的 socket 无超时，
行业表 24h TTL 缓存第一次刷新挂起时，主流程无限等。

**修法**：

* `_fetch_baostock_industry_map()`：daemon 工作线程 + `join(timeout=8s)`，
  超时抛 `TimeoutError`，主线程一定返回；
* `_get_industry()` 捕获失败 → 行业中性（空缓存）+ 失败重试 TTL 1800s。
  初版设 60s，日志空档显示单次扫描内仍重试 5 次、多耗约 4 分钟——
  **失败重试间隔必须大于单轮任务耗时**，否则"降级"本身变成新的性能问题；
* 判据 `tests/test_industry_fetch_bound.py` 3 例（有界返回 / 失败降级且负缓存 / 正常解析）。

**测试坑**：conftest 把 `lib.data.data_layer.time.sleep` 全局替换成 no-op
（`time` 是标准库模块本身），用 `time.sleep(3)` 模拟慢调用会失效——
改用 `threading.Event().wait(3)`。

**推广**：任何第三方 socket 调用都必须有界；「修好一个分支后性能突然变差」
往往说明那个分支以前根本没执行过。先用 faulthandler 抓栈，别猜。


### P-107 · 同一慢接口被多处调用，先 profile 再优化

**现象**：P-106 修完后扫描不再超时，但量价突破阶段仍耗 208s；
单次 `ScoreCard.score` 实测 **13.77s**。

**根因**：cProfile 显示其中 6.2s 花在 `akshare.stock_zh_index_daily`
（`MarketFilter._compute_l1` 与 `_get_market_return` 各拉一次，每次 ~3.1s）。
同一份沪深300 日线在一轮任务里内容不变，却被重复网络拉取。

**修法**：`_get_hs300_frame()` 带 10 分钟 TTL 缓存原始帧，两处调用方共用；
`as_of_date` 截断仍由调用方各自执行——缓存原始帧不会引入前视偏差。
判据 `tests/test_hs300_frame_cache.py` 2 例（只拉一次 / 截断语义不变）。

**推广**：性能问题先 profile（`cProfile` 按 cumtime 排序、`faulthandler` 抓挂起点），
不要凭直觉优化。缓存数据帧时务必确认「可变参数的处理发生在缓存之后」，
否则会把防前视机制一起缓存掉。


### P-108 · 交易日判定硬编码死路径，兜底方向反了：节假日照跑推陈旧信号

**现象**：架构评审（`docs/audit/architecture-review-2026-11-20.md` P0-3）发现
`scripts/daily_scan.py:41` 硬编码 `D:/TDX/vipdoc/sh/lday_4/sh600000.day` 判定交易日；
该包 2026-11-14 起停更（`update_tdx_data.py:148` 注释禁用）且目录已随 ADR-0017 删除。
文件不存在时走兜底「仅跳过周末」→ **节假日的工作日照常跑扫描并推送上一交易日的
陈旧信号**。评审 v1 曾误判为「永远跳过不跑」，对抗性复核实测兜底方向相反——
「兜底会静默接管」比「兜底不跑」更危险。同链：`reporter/scheduler.py:9` 晨报门只看
周末；`run.py` 盘后扫描无任何交易日门。

**根因**：交易日判定散落三处，唯一带日历语义的实现依赖已死的本地 TDX 文件；
兜底语义（跑/不跑）从未被机器判据钉住。

**修法**：`lib/data/trade_calendar.py` 单一真源（akshare 新浪日历 +
本地 JSON 缓存 + 刷取 daemon 守卫（ADR-0019 模式）+ 失败显式退化周末判断 + warning）；
三处消费端统一委托；`run.py` 加交易日门 + `--allow-non-trading-day` 显式旁路。
判据 `tests/test_trade_calendar.py` 13 例。

**测试坑**：tests/conftest.py 的 `logging.config.fileConfig` 把 `lib` logger 配成
`propagate=0`（自带 console handler）→ pytest caplog（挂 root）**收不到任何 lib.*
记录**；判据日志断言必须直挂目标 logger 的 Handler，caplog 仅对根命名空间（如 run）有效。

**推广**：① 时间语义的兜底方向（跑/不跑）必须与主语义一起被机器判据钉住，
否则死源会让兜底静默接管；② 判定类散落实现先用 grep 收口再动手；③ 测试里断言
`lib.*` 日志永远不要用 caplog（fileConfig propagate=0），直挂 handler。


### P-109 · 灰度开关默认值翻转前，必须清点「写了没接线」的共享可变状态

**现象**：架构评审（`docs/audit/architecture-review-2026-11-20.md` P0-1）准备把
`FEATURE_ASYNC_OFFLOAD_DEFAULT` 从 "0" 翻转为 "all" 时，对抗性复核发现
`audit_logger._LOG_LOCK`（:26）定义后**从未 acquire**——`:23-25` 注释声称
「log_scan / get_recent_scans 等关键写入持锁」，与代码不符（2026-06-08 的修复
只写了锁没接线）。off 默认下全串行跑不出问题；一旦翻 all，线程池并发写
`cache/audit.db` 撞 `database is locked` → 被 `except → logger.error → return 0`
吞掉 → **审计记录静默丢失**。

**根因**：安全阀默认值长期不翻转，「接线了但没启用」的缺陷在默认路径下不可达，
永远不会暴露；翻转把潜在并发路径一次性全部点亮。

**修法**：log_scan 抽 `_log_scan_locked` 写入体 + `with _LOG_LOCK` 包裹；
`_get_db` 加 WAL + busy_timeout=5000；判据 3 例（24 线程并发零丢失 /
WAL 生效 / **锁接线 AST 守卫**——`inspect.getsource` 断言 `with _LOG_LOCK`
真的出现在 log_scan 里，防再次静默脱锁）。

**推广**：① 「修了但默认不可达」的代码等于没修——翻转/启用任何长期 off 的
开关前，先 grep 它的执行路径上所有共享可变状态；② 接线类修复必须带
**接线守卫判据**（断言锁/调用真的出现在源码里），注释与代码不符比没注释更危险。


### P-110 · L9/L10 门禁留下的暖 API 缓存，会污染随后的 dual-run 全量判红

**现象**：批次 C2 实测（2026-11-20）：跑完 `offload_switch_diff --check` 与
`concurrency_probe --check` 后立刻跑 L1 全量，`test_gate_reports_no_drift_twice`
判红；**隔离复跑该文件即绿**。`cache/api_hs300.json` 的 mtime 与门禁执行时刻吻合。

**根因**：L9/L10 以真实数据打真实端点，会在 `cache/api_*.json` 留下暖缓存；
dual-run 门禁的链路对拍依赖「冷重算 vs pin 基线」，暖缓存让链路吃到缓存值 →
与基线漂移。RULES §4.1 已记载「L1 与门禁并发会假红」，本坑证明**先后串行也不安全**：
门禁的副作用（暖缓存）会跨进程存活到下一次全量。

**修法（流程级）**：全量门禁顺序 = **先 L1、后 L9/L10**；或门禁后
`del cache/api_*.json` 再跑 L1。不改基线、不改判据（G3）。

**推广**：门禁脚本的「只读」假设要验证——凡写过 `cache/` 的门禁，都要在
RULES 里登记其对后续判据的副作用；「串行」不等于「无共享状态」。


### P-111 · Windows 下 subprocess 裸 "npx" 不解析 .cmd shim，源死亡被误诊为「缺 Node」

**现象**：westock 数据源在本机「全灭数月」，registry 注释与 CLAUDE.md 记载为
「adata·westock 缺 Node」（ADR-0027 源栈审计同口径）。实测（2026-11-20 批次 C3）：
`shutil.which("npx")` 能解析出 `C:\Program Files\nodejs\npx.CMD`——Node 明明装了；
真因是 `subprocess.run(["npx", ...])` **不走 PATHEXT**，裸 `"npx"` 直接
`FileNotFoundError`，被 `_run_westock` 捕获后误报「npx 未找到，请安装 Node.js >= v18」。

**根因**：Windows 上 `subprocess` list 形式不做 shell 式可执行名解析（`.cmd/.bat`
shim 不可见）；报错文案把环境问题误诊成「没装」。

**修法**：`data_westock._resolve_westock_cmd()` 用 `shutil.which("npx")`（沿 PATHEXT
解析完整路径）生成命令，WESTOCK_CLI 环境变量覆盖优先级不变。判据
`tests/test_sina_fast_fail.py`（which 解析/缺失回退/环境变量覆盖/本机 import 期已解析）。
复活后 westock 经 registry 端到端实测返回完整 6 列 schema（有效日线源 2→3）。

**推广**：Windows 下 `subprocess.run(list)` 调 `.cmd/.bat` shim 一律先
`shutil.which`；「缺 XX」类日志先跑 `shutil.which("XX")` 再下结论，别让误诊写进文档
（本例误诊进了 ADR-0027 与源栈注释）。


### P-112 · 第三方 CLI 升级改表头语言（中文→英文），映射层静默失配

**现象**：修活 westock（P-111）后发现 registry `col_map` 全是中文键（日期/开盘/收盘），
而 CLI 1.0.4 实测返回英文表头（`date/open/last/high/low/volume`）——列名映射全部落空；
`last`→`close` 缺失使 6 列校验不过 = 整链白取且**不报错**。更隐蔽的是
`get_kline` 的 `as_of_date` 截断只认 `"日期"`，英文表头下**静默跳过 = 回测前视偏差**。

**根因**：第三方 CLI 无版本化契约，升级改了表头语言/列名；映射层对「预期列不存在」
静默降级（与 P-107 腾讯 rows 错位同族：错位/缺失不报错）。

**修法**：双表头映射（`"last"→"close"` + 中文键保留）+ `as_of` 日期列双形态兼容
（`日期` 或 `date`）；成交量单位实测钉死 = 手（sh600519 westock 42692 vs 新浪
4269153 股，比值恰 100.0），registry 分支**就地 ×100**（勿放共享 `_normalize_columns`，
否则新浪/腾讯重复 ×100）。判据 3 例（映射/×100/as_of 英文表头截断）。

**推广**：第三方子进程数据源必须把「表头快照」钉进判据（升级即判红）；
对「预期列缺失」要么显式抛错要么打 warning，静默返空帧是前视与漏数据的最爱。


### P-113 · conftest 进程内禁网挡不住 subprocess 子进程——westock 复活后 L1 静默 +70s

**现象**：批次 C3 修活 westock（P-111）后，L1 全量时长 81s→130s（+60%），无任何
用例失败所以无人察觉；C6 的 deadline 判据跑 run_all_strategies 全链时单测 115s 才定位。

**根因**：conftest 默认禁网 patch 的是**pytest 进程内**的 `socket`/`create_connection`
——`subprocess.run(["npx", ...])` 起的**子进程有自己的网络栈**，完全逃逸这道墙；
npx 包缓存命中后虽不再下载，但每次起进程 + CLI 内部网络重试仍 ~1-2s/次。
multi-source 链（量价 top30 + 多因子 top50 + ML 富集）每次取数都会轮到 westock 分支
→ 全量累计 80+ 次真子进程调用。

**修法**：conftest autouse `_block_westock_subprocess` 桩掉
`data_westock._run_westock` 返回空串（multi-source 链照常降级，westock 分支 ≈ 快速 miss）；
westock 自身判据显式 patch（`tests/test_sina_fast_fail.py`），互不冲突。

**推广**：①「测试禁网」的真实边界 = 进程内 socket；凡测试期会起**子进程**的代码路径
（npx/pip/git/curl），必须在 conftest 显式桩掉或加白名单，子进程是禁网墙的天然隧道；
② L1 时长是判据，不是感觉——全量时长突变（>20%）要像失败一样排查。


### P-114 · 缺失被 `fillna(0)` 伪装成「真实零值」⇒ 以假中性分进入四维加权

**现象**：2026-11-20 实测取证——资金流市场批量层（同花顺即时）落盘的
`cache/fundflow__market.parquet` 共 5216 行，其中 **5212 行是全零**（仅 4 条非零）；
而评分层长期「正常」，无任何告警：资金面维（L2，权重 35%）每天以
`main_net_ratio=0` 参与四维加权。

**根因**：四层叠加，每层单独看都「合理」：

1. **口径漂移**：上游「股票代码」列是 **int64**（301132 / 300812 …），前导零在
   解析阶段即丢失（缓存 key 变成 '1' / '10' / '858'），而查询侧用 6 位代码 ⇒
   深市 000/002、创业板 300、北交所 4/8/92 **批量层 100% MISSING**，直接落默认 0。
2. **`fillna(0)` 是伪装器**：上游此刻资金流列几乎全为 NaN（接口只回了代码与最新价），
   解析层 `pd.to_numeric(...).fillna(0)` 把「缺失」转成「有效零值」⇒ 5212 条假零值。
3. **评分层没有「缺失」这个状态**：`score_capital_flow` 的 `elif main_net_ratio > -0.05`
   把 0 判为「中性」⇒ **缺失 = 1.0 分**。实测：缺失 1.0 / 真实中性 2.0 /
   真实强流入 5.0 / 真实强流出 0.0 ⇒ 缺失与真实中性只差 1 分，**不可区分**。
   且 `ff = fund_flow if fund_flow is not None else ...` 使全零 dict 不是 None
   ⇒ 逐股重试永不触发，缺失静默固化。
4. **退化监控看不见**：`check_factor_health` 只统计 `('RSI','动量5','置信度','综合评分')`
   四列 ⇒ 资金面/基本面/新闻面的退化**根本不在监控范围**。

**修法**（本批 P0，2026-11-20）：
- `lib/data/market_utils.normalize_code()` = **代码归一单一真源**（去 sh/sz/bj 前缀 +
  `zfill(6)`）；解析层 / 缓存读回层 / 查询层 / `data_source_registry._store_key`
  四处共用。存量脏缓存读回时归一，**不作废已有数据**。
- **缺失显式化**：`FUND_FLOW_MISSING`（`main_net_ratio=None`）显式标记 + 数据层
  `FundFlowUnavailableError` **显式抛错**（熔断中 / 全源失败不再返回全零）；
  「真实零值」仍正常返回零 dict —— 两者由异常路径区分（沿用 ADR-0016 的原意）。
- **解析层不再 fillna(0)**：净额全 NaN 的股票 = 该股数据缺失，不进结果；
  缓存读回跳过全零条目（存量脏表不再当真源，触发重新拉取）。
- **评分层剔除缺失维**（口径经用户裁定）：`comprehensive_score` 支持
  `capital_flow=None` ⇒ 该维从加权中**剔除**、其余三维按原权重比例归一；
  `breakdown.capital_flow=None` 与 0 分可区分。

**证据**（可复跑）：

| 读数 | 修前 | 修后 |
|---|---|---|
| 批量层 6 位码命中率（10 只样本） | 4/10 | **8/10** |
| 脏缓存可用条目 | 5216（全部冒充有效） | **4**（其余剔除） |
| 缺失的评分表达 | 1.0 分假中性 | **剔除该维**；综合分 4.65 → 6.62 |
| 0 开头代码批量层 | 100% MISSING | 命中 |

**附带教训（浮点 · 阈值边界）**：把四维加权改写成 `sum(v*w)/sum(weights)` 会**动阈值
边界**——权重和 `0.35+0.35+0.20+0.10` 在 IEEE754 下是 `0.9999999999999999`，
除以它会把 `8*0.35+8*0.35+8*0.20+8*0.10` 从 `7.999999999999999` 变成 `8.0`
⇒ 评级在 8.0 阈值从「下一档」翻到「上一档」
（`tests/features/test_scoring_contract.py` 冻结了该既有行为）。
⇒ **正常路径必须保留原表达式**，归一只在缺失分支启用。

**推广**：
① 「缺失」与「真实零值」必须在数据层用**不同表示**（None / 标记 / 异常），
   `fillna(0)` 与 `or 0` 是最常见的两种伪装器；
② 上游返回空表（全 NaN）时，解析层不得把 NaN 当 0 —— 先判「这批数据有没有值」；
③ **监控列必须覆盖所有维度**，否则「某维长期缺数据」永远不会被告警发现
   （本项目只查 4 列，资金面退化因此长期无人发现）；
④ 代码归一要有单一真源；上游给 int64 列 ⇒ 前导零必丢，不是「少写了几位」；
⑤ 缺失维的加权口径（剔除+归一 / 记 0 / 剔除该股）属**产品决策**，
   会实盘改变选股结果，须走 ADR + L5 对拍，不得由实现者默认选定。


### P-115 · 文档分层靠「人自觉」必然回漂 —— 要机器判据把「单一真源」变成可执行

**现象**：2026-11-20 文档分层重构（新增 `AGENTS.md` 作 AI 行为唯一真源，
`CLAUDE.md` 改 `@AGENTS.md` 导入层，`RULES.md` 收窄为例外登记，`README.md` 扩为人读入口）。
分层本身顺利，但**写完判据脚本首跑即报 101 项**：其中混着三类完全不同的东西 ——
① **真断链**（`CLAUDE.md` 从 471 行改到 104 行 ⇒ 审计报告里指向该文件第 443 行、
   第 162 行等**行号引用全部越界**）；② **判据自身缺陷**（绝对路径 skill 目录、
   模板占位符 `NNNN-title-kebab-case.md`、仓外路径被当成仓内断链）；③ **既有噪音**
   （`docs/audit/` 里引用了 5 个从未存在的审计文件，与本次重构无关）。

**根因（写判据的三条硬约束）**：

1. **「当时快照」与「活引用」必须分开**。行号引用（`file.md:123`）= 当时的取证快照，
   **append-only**，改它等于篡改证据 ⇒ 历史面（`docs/adr`、`docs/audit`、
   `docs/plans`、`docs/archive`、`TODO.md`）只**告警不判红**；
   而**章节号**引用（`file.md §2.3`）是活引用，**必须可解析** ——
   旧章节号由 `RULES.md` §3 迁移对照表承接（故对照表要按**旧编号原文**列，
   不能只写「§1·五」而漏掉 ADR 里实际引用的「§1.5/§1.6/§1.7」）。
2. **裸 basename 约定要么判据能解析，要么全仓统一写全路径** —— 不能两样都有。
   本仓 `pitfalls.md` 是裸引用（实指 `docs/refactor/pitfalls.md`）⇒ 判据须按
   「basename → 唯一核心文件」解析，否则把「简写」误判成断链。
3. **判据会被扫到自己的文件** ⇒ 自证用的注入样本若写成「文件名 + 冒号 + 行号」的
   字面量，会被自己扫到 ⇒ **判据永远红**（空判据的另一种形态：不是恒绿，是恒红）。
   注入字面量必须**拼接书写**；文档正文里提到这类行号引用时也要改用
   「第 N 行」的中文表述，否则同样触发自指。
   ⚠ **本条 P-115 自己就踩了这个坑**：初稿正文里写了具体行号字面量，
   写完跑判据立刻判红 2 项 —— 判据抓自指的灵敏度得到实证（改写后转绿）。

**修法**：`scripts/doc_structure_check.py`（+ `tests/features/test_doc_structure.py` 8 例）——
五文件齐备与专属锚点、`@AGENTS.md` 导入、交叉引用断链（FAIL/WARN 分级）、
跨文件 ≥8 行重复段落、规约 ID 唯一；并带**自证用例**（注入核心文件行号越界 ⇒ 必须判红）。

**附带发现**：旧 `RULES.md` 里**回测调用规约**（B1–B5）与**行为不变**（B1–B3）
**编号同名** ⇒ 迁移时必须消歧（回测调用改 **RB1–RB5**），否则「B1」在两个语境里
指向不同规则，引用即歧义。

**推广**：
① 写判据先问「红的是哪一类」：真断链 / 判据缺陷 / 既有噪音 —— 三类修法完全不同，
   **一律改文档会把取证快照改坏**（等于伪造历史）；
② 行号 = 快照（append-only），章节号 = 活引用（必须可解析）—— 判据按这个分；
③ 判据自身在扫描范围内 ⇒ **自指样本要拼接书写**，否则判据永远红；
④ 文档分层不能只靠「写清楚」：没有机器判据的单一真源约定，一次改动就会重新长出副本；
⑤ **规约编号必须全仓唯一** —— 迁移/改名时先查同名，否则新文档继承旧的歧义。

### P-116 · 全市场同步用「逐只串行 + 无断点」结构上就跑不完

**现象**：2026-11-20 项目内数据仓（ADR-0033）落地后，全市场同步始终「未完成」——
日志记「腾讯被 WAF 拦时每只 ~7s，300 只 10 分钟只完成 80 只」。
`data_sync.py` 原始实现是**逐只同步、无进度记录**，中断后必须从第 1 只重来。

**侦察取证（2026-11-20 20:xx）**：
- `get_realtime_quotes()` 全市场 **5579 只**，源默认顺序把北交所 `920xxx` 排在**最前**，
  逐只约 1.9–2.1s；活跃标的（300750/600519 等）串行约 1.9s/只；
- 4 并发拉 16 只 **8.8s、零失败**（约 1.8 只/s）——瓶颈是串行等待网络，不是源站限流。
  按 1.8 只/s 估算，全市场约 50 分钟可完成，而串行则需 ~3 小时。

**根因（两条结构性缺陷，不是数据问题）**：
1. **逐只串行**：网络 IO 等待占满 wall-time，却不占 CPU；4 并发即提速 3 倍，证明并发是正解；
2. **无断点**：进度只在内存里累加，进程一断（超时/关机/Ctrl-C）全部归零，
   5000+ 标的的窗口内几乎必然中断 ⇒ 「跑完」在结构上不可能。
   ⚠ 这与「patched symptom」相反：加 sleep/重试都救不了，因为瓶颈是**架构**不是失败率。

**修法**（`scripts/data_sync.py` 增强，保持旧 CLI 兼容）：
- 新增 `--workers N`（>1 走 `ThreadPoolExecutor`），默认 1 不改旧行为；
- 新增 `--resume`：进度落 `cache/data_sync_progress.json`（gitignored），按**当天日期隔离**，
  每 50 只原子落盘，重跑跳过已完成标的；
- 单只最多重试 1 次，失败不影响其它标的；成功/失败/跳过分别统计；
- `_universe("all")` 已按成交额降序，活跃标的优先落盘（但断点进度按 code 去重，顺序不敏感）。

**验证**：top50 并发 50/17.5s/零失败；断点复跑第 1 次建立进度、第 2 次 `skip=50` 且 0s。
全市场后台跑（workers=4/resume）18 分钟落盘 **886 只**（kline 仓 95→926），
但因**外部源栈同时恶化**未完成剩余 4600+：腾讯双主机 501 WAF 拦（ADR-0032）、
baostock 再次拉黑、tushare 无 daily 权限、akshare 8.8s 空帧；
唯一可用的 westock **单线程 8/8 稳（~5s/只）、4 并发触发限流（8 只 4 空）**。
⇒ 结论：**并发脚本本身正确**（源好时 1.8 只/s），但当前无任何源能同时满足
「稳定 + 快速 + 覆盖全市场」——剩余 4600 只只能 westock 单线程 ~6h 或等源恢复。
断点已落 cache，随时可续跑，无需从头。**这不是代码缺陷，是外部数据源阻塞。**

**收尾（同日 23:xx）**：腾讯 WAF 反复横跳（通↔断），任务挂跑 1h34m 后
**成功落盘 5306 / 5579 只（95.1%）、273 只失败**。失败标的未进 done ⇒ 可续跑补缺。
westock 单线程补缺 26m：**272/273 成功**，最终 **5578 / 5579（99.98%）**。
唯一缺口 **920309 菊乐股份（次新股）**：新浪仅 4 根、腾讯 1 根、其余源 0 行，
`_validate_dataframe` 的 `min_rows=10` 门槛正确拒绝——**不为凑满 5579 而绕过校验写入稀疏帧**（那是硬凑）。
关键陷阱：`get_stock_hist_multi_source` 在 `source != "auto"` 时 `use_local=False`，
**取到数据也不写本地仓** —— 若只用 `--source westock` 兜底而不补写，缺口永远补不上。
修法：`data_sync.py` 新增 `--source`，指定单源时显式 `local_store.write_frame("kline", ...)` 补写回
（不碰取数层核心，避免改回测口径）。3 只小批验证 3/3、各 250 行落盘。
随后 `--source westock --workers 1 --resume` 补 273 缺口（westock 并发限流，必须单线程）。

**推广**：
① 判断「跑不完」先问是**速度**还是**可续**问题：串行网络 IO 靠并发，长任务靠断点，两者缺一不可；
② 断点进度要**按业务日期隔离**（跨天重跑应全量刷新，不是续昨天的）；
③ 并发前先小批测速+失败率，别假设源站会限流——实测 4 并发零失败就别加保守 sleep；
④ `cache/` 是 gitignore 的运行时区，进度文件放这里不污染版本库；
⑤ **指定单源兜底必须验证「取到 ≠ 落盘」**：取数层的本地写回常绑定 auto 路径，
   显式源绕过后要手动补写，否则补缺任务看起来成功、仓里却没数据；
⑥ 外部源反复横跳时，让带断点的任务持续挂跑比「反复重启」更优——恢复窗口自动加速，进度不丢。

### P-116 附：financial / fundflow 全市场铺开侦察（同日 01:2x，用户「做」）

- 脚本增强：`_load_progress/_save_progress` 泛化为按 dataset 隔离进度文件；
  `sync_financial/sync_fundflow` 加 `_sync_json_dataset` 统一断点骨架 + 异常捕获
  （原 fundflow 无 try/except，`FundFlowUnavailableError` 会中断整个循环——真缺陷，已修）。
- **financial 全市场实测失败**：220 只仅 1 成功（小批 4/5 是因那几只有缓存）。
  根因：`get_stock_fundamentals` 兜底链是 **TDX(已删 P-104) → baostock(再拉黑)**，
  东财 `get_em_financial` 仅在两者成功后作「字段补充」调用（442/453/474 行），
  **从不作为独立兜底源**——即使东财对 000002 返回完整 dict 也照样返回 None。
  ⚠ **不把东财提升为一等兜底源**：其返回缺 ROE/ROA 等关键字段，会产出残缺数据污染评分口径；
  且改评分源栈需 ADR + L2/L3 门禁，超出「铺数据」范畴。宁可显式缺失（P-114 教训），不硬凑。
- **fundflow 全市场实测不可行**：akshare 批量源 `stock_fund_flow_individual("即时")`
  返回 5216 行但「净额/成交额」列几乎全 NaN（仅 4 条非零）——上游无数据。
  `_parse_market_fund_flow` 正确拒绝 fillna(0)（P-114）；逐股路径 15s/只超时 ⇒ 20h 不可行。
- 结论：财务/资金流本地仓维持代表标的（financial 5 / fundflow 2），全市场铺开**被上游数据源阻塞**，
  不是脚本问题；断点/异常捕获增强已保留（根因修，非空转）。
- **后续（01:2x→04:xx）**：baostock 恢复 + 东财一等源落地（ADR-0035）后，financial 全市场重跑成功推进。

### P-117 · 「源缺字段」的判断要看原始 API，别被裁剪函数误导

**现象**：评估「东财能否当一等财务源」时，我最初的结论是「东财返回缺 ROE/ROA，
残缺数据会污染评分」——并据此写下「不改评分链」的决策（P-116 附段）。
一小时后发现这个判断**是错的**：东财原始数据字段齐全，缺字段的是项目内的**裁剪函数**
`get_em_financial`（只映射 7 个字段），不是 API 本身。

**取证**：直接调 `data_eastmoney_fin._fetch_em_financial("000002")`（emweb ZYZBAjaxNew 原始记录），
字段含 `ROEJQ`（ROE 加权）、`ZZCJLL`（总资产净利率=ROA）、`XSJLL`（净利率）、`XSMLL`（毛利率）、
`ZCFZL`（资产负债率）、`TOTALOPERATEREVETZ`/`PARENTNETPROFITTZ`（增长率）——标准形态所需字段全覆盖。

**根因**：把「封装函数返回的字段集」等同于「数据源的能力边界」。封装是为特定消费者做的裁剪，
不能据它判定源能力——这是典型的「透过受限视图看全貌」误判。

**修法**（ADR-0035）：新增 `get_em_financial_full()` 返回完整标准形态（不改 `get_em_financial` 的
7 字段契约，L5 基线依赖它）；`get_stock_fundamentals` 兜底链扩展为
TDX → baostock → 东财完整源 → None。验证：`get_em_financial_full("000002")` ROE/ROA 非空；
北交所 920087 走 `source=eastmoney_full`；**L5 评分基线 9 入口/285 样本零漂移**、
L2 契约不变 ⇒ 加法修复不改口径。

**推广**：
① 判断「某源能不能满足需求」必须看**原始 API 返回**，不是项目内的裁剪/适配函数；
② 「加法修复」（新增 full 函数 + 扩展兜底）比「改既有契约」安全——L5/L2 零漂移可证；
③ 外部源状态会变（baostock 拉黑→恢复），基于「当下不可用」写下「永久不改」的结论要留复核钩子。


### P-118 · backtest/full.py 模块级全局猴补丁：import 期永久替换 data_layer.get_stock_hist

**现象**：批次 C12 新判据（patch `data_layer.get_stock_hist_multi_source` 后调
`get_stock_hist`）在 `tests/test_ab_testing.py` 先跑的任何全量序下恒拿到空帧。
探针实测污染态 `data_layer.get_stock_hist.__globals__ is not data_layer.__dict__`
——初步误判为「双模块实例」（import 期 GC 探针证明模块对象仅 1 个），真正机制是
**函数对象在 import 期被顶替**：顶替函数的 globals 归属 `backtest/full.py` 自己的命名空间。

**根因**：`backtest/full.py` 模块级执行 `_dl.get_stock_hist = _patched_get_stock`
（注释自称「覆盖为本地版本（避免网络回退）」）。触发链 =
`backtest/__init__.py:30 from backtest.full import run_full_backtest` → 任何
`import backtest.*`（API 路由 / CLI / ab_testing 链）即触发，**永不还原**。
ADR-0017 删除本地 TDX 后，补丁函数读不存在的 `D:/TDX/vipdoc` → 恒返回空帧 →
全局 `get_stock_hist` 数据断供。

**修法**：删除模块级猴补丁三件套。grep 证实 full.py 自身数据路径走
`get_stock_hist_multi_source`（:288/:363），对被替换符号零内部调用点；
batch_b 门只 patch `run_full_backtest`，不依赖补丁。回归守卫：
`tests/test_c12_leftovers.py::_double_data_layer_dict` 断言
`get_stock_hist.__globals__ is data_layer.__dict__`（复发即判红）。

**推广**：①模块级「其他模块.属性 = ...」= import 期全局副作用，等效于把一个
不可还原的猴补丁焊死在别的模块上——除非有显式还原机制，禁止在模块顶层写
跨模块赋值；②排查此类问题用 `func.__globals__ is module.__dict__` **身份探针**，
GC 数模块对象会把人误导向「双实例」假说（本坑实测走了弯路）。

---

## 十、复盘批次追加（2026-11-21）

### P-119 · 后台扫描线程 × 进程级单例：失效计数跨用例/跨线程污染，scan 链策略被静默降频

**现象**：`test_gate_reports_no_drift_twice`（L3 gate 行为判据）全量红、单跑绿，
且红/绿随文件组合与时序变化——二分实测 `{a 段全 11 文件}+gate` 红，而
`{a1..a9}+gate`、`{api}+gate`、`{api}+{attribution}+gate` 全绿。gate 漂移路径全部在
scan 链：部分策略结果 DataFrame → `None`、`expected_vol` 漂移、策略暴露计数 10→6。

**根因**（两层叠加）：
① `pipeline._failure_tracker` 是模块级单例（`persist=False` 不读盘 ⇒ 重启才清零），
失效计数在同一 pytest 进程内**跨用例累积**，`should_skip` 阈值 3 ⇒ 先跑的用例
凑满 3 次「无候选股」，其后任何 scan 链（含 gate 的 capture_scan）都会静默少掉
被降频的策略（P-100 同族复发）。
② 污染的**源頭**是 `GET /api/scan` 无缓存时 spawn 的 daemon 线程
（`routers/scan.py:26` → `routers.common._run_scan_background`）：test_api 对 3 个
preset 各调一次，线程与后续用例**并发**跑真实 `run_all_strategies`（禁网 → 全空 →
`record_failure`），跨线程写进共享单例——conftest 的用例间 reset 管不住
「测试运行中途落地的跨线程写入」，时序竞态 ⇒ 组合红绿看运气。
附带发现：`save()` 无条件写生产 `cache/strategy_failures.json`（路径按 `__file__`
拼接，conftest 的 `CACHE_DIR` 重定向罩不到）——P2-5 曾在 test_scan_deadline
局部换 fresh 实例规避，未收口默认机制（P-23 预言应验）。

**修法**：conftest 两个 autouse fixture，按 westock/host_health 既有范式：
① `_reset_strategy_failure_tracker_between_tests`——用例间 `tracker.reset()`
（pipeline.StrategyFailureTracker 新增 reset()）+ 把单例 `_path` 重定向到 tmp
（测试永不写生产缓存，P-80 纪律补全）；
② `_stub_background_scan_thread`——patch **使用方** `routers.scan` 的
`_run_scan_background` 绑定为 no-op（I4：星号导入绑定在使用方命名空间），
端点契约不变（仍 spawn + 立即返回 `scanning: True`，test_api 只断言 != 400）。

**推广**：①凡「线程体调用共享可变单例」的端点，测试里必须桩线程体而非放任真跑
——单例的用例间 reset 管不住并发写入；②进程级单例的用例间清零要做成
**conftest 默认机制**（P-23），逐测试局部换实例（test_scan_deadline 的做法）
迟早漏掉下一个调用方；③单例的持久化路径若绕过 `CACHE_DIR`，缓存目录隔离
fixture 就失效——新单例登记时必须同时进 conftest 重定向清单。
**自动化路径**：① 机器判据 = conftest autouse fixture（本条即判据）+
`test_gate_reports_no_drift_twice` 作金丝雀（复发即漂移判红）。

### P-120 · 判据测试的 live 侧依赖生产兜底路径：重构删掉兜底 = 判据静默失效

**现象**：`test_pinned_index_differs_from_live_and_restores`（P-89 的复发判据）
2026-11-20 隔离复跑绿、2026-11-21 隔离也红——代码未动、上游未动。
失败点 `live["close"]` KeyError，pandas 空帧上取列。

**根因**：该判据的 live 侧直接调恢复后的 `pipeline.get_index_hist`，在测试环境
（`QUANT_LOCAL_STORE=off` + 默认禁网）下**唯一数据来源是 `cache/index/` 兜底**。
ADR-0033 本地仓收官重写 `get_index_hist` 为「本地仓 → 在线」、删除旧 cache 兜底
后，测试环境 live 侧既无本地仓（off）也无网络（禁）⇒ 恒空帧。
11-20 的「隔离绿」只是当时 cache 文件尚在——P-89 的判据测试自己踩了 P-89
（取证链路依赖运行时状态）。台账 11-20 的两个归因（上游帧形状漂移 / 顺序污染）
均不成立，真机制 = 兜底路径被重构移除。

**修法**：live 侧在**在线边界** `data_layer._get_index_hist_online` 注入固定
~3600 合成帧（monkeypatch 定义方模块属性；不碰 pipeline 使用方绑定，
恢复断言 `is original` 保持有效）。断言一行未动（G3），只换数据来源。

**推广**：①判据测试的每个取数点要回答「在测试环境下（禁网 + 本地仓 off）
数据从哪来」——答不出来 = 迟早假红；②重构取数链时，grep 依赖方不能只看
生产调用方，**测试里的判据也是调用方**（本坑 = ADR-0033 重构的直接回归）；
③「昨天绿今天红且两边代码都没动」先查**环境状态**（缓存文件 TTL、本地仓、
数据源健康），再查代码——与 P-89/P-92 同族。
**自动化路径**：① 机器判据 = 本判据自身（在线边界注入后确定性，复发即红）。

### P-121 · 中文单位数字串被 to_numeric 整体 coerce 成 NaN：解析器制造「上游失效」假象

**现象**：资金流市场批量层（ADR-0018）2026-11-20 实测「5216 行但净额几乎全 NaN
（仅 4 条非零）」，归因为上游瞬时失效（P-99 家族口径）。2026-11-21 复盘修
fundflow 同步时复现同现象，**直接查原始 API 帧才发现**：净额/成交额列是中文
单位数字串（`"1.11亿"` / `"-9225.10万"`），`pd.to_numeric(errors="coerce")`
把它们整体打成 NaN——原始帧 0 NaN，**「上游缺数」是解析器制造的假象**。
二次教训（同日）：THS 周六晚间清算时段返回「--」占位、页数从 105 变 70，
可用性分钟级抖动——「上游恢复/上游挂了」两个第一印象都不可靠。

**根因**：解析层对人读格式数字串（万/亿/千分位）没有归一；NaN 由 coerce
制造而非上游缺失，P-114 的「缺失显式化」语义建立在被污染的输入上——
幸而缺失/零值判据方向没错，修复解析后语义自然成立。

**修法**：`data_layer._cn_number_to_float`（亿=1e8 / 万=1e4，兼容纯数字与
千分位），净额/成交额先过它再判缺失；解析不了的值（"--"/空串）仍按真实
缺失处理，不进结果（P-114 语义不变）。

**推广**：①「上游缺列/缺值」的结论必须以**原始 API 帧**为证——P-117 推广段
早已写明，但本坑证明它在归因与复盘两个环节都会反复被骗；遇到 NaN 先问
「是我 coerce 出来的吗」；②人读格式（万/亿/％/千分位）在解析层统一还原，
禁止散落调用方各自处理；③源可用性结论必须标注**抓取时刻**，清算/盘后
时段的表结构与盘中可能不同。
**自动化路径**：① 机器判据 =
`tests/features/test_fund_flow_code_and_missing.py::test_parse_market_fund_flow_handles_cn_unit_strings`
（复发即红）。

---

## 十一、Wind 第三方对照追加（2026-11-21）

> 来源：用 Wind 插件对 `data/store/` 做第三方数值对照（复权口径 + 资金流 P-121 复核）。
> 对照结论：kline_raw（不复权）与 Wind 逐值一致；本地前复权 = 腾讯等差式（Wind = 等比式，
> 口径约定差非缺陷）；以下三个**真实缺陷**为对照发现。C16 断网演练 PASS 而对照才炸——
> 演练证「链路自给自足」，第三方对照证「数值正确」，两者互补缺一不可。

### P-122 · 腾讯 K线 volume 单位按板块不同（科创板=股），解析层一刀切 ×100

**现象**：Wind 第三方对照（2026-11-21）发现 610 只 688xxx（科创板）的本地 kline
（前复权仓）volume = 真值 ×100，kline_raw（不复权仓）与 Wind 逐值一致——量比/换手/
量价类因子在科创板静默全错，不报错。直接 curl 腾讯原始报文证实：`sh688001 qfqday`
volume=10276186（=股，与 Wind 同值）；`sh600519`=67229（=手，Wind 6722940 股 ÷100）——
**同一接口的 volume 单位按板块不同**。

**根因**：`lib/data/data_kline_web.py::parse_tencent_kline` 注释「量单位=手」并对所有
标的无条件 `volume_scale=100.0`——该契约只在沪主板/创业板成立，科创板是股。
C15 全市场同步把 ×100 落进 kline 仓（5578 只中 610 只）；raw 仓走新浪（已是股）不受影响。
「上游是手」是对主板样本的归纳被当成了全市场契约——**源契约按子集归纳未全量验证**。

**修法**（待 ADR-0036）：解析层按板块判单位（688/689 开头 scale=1，或 volume×vwap≈amount
交叉自证）；610 只存量重写。改回测输入 ⇒ 必须走 ADR + L4 基线重建，不可静默修。

**推广**：① 多源报文契约（列序/单位/编码）按**板块**全量验证，不许按样本归纳——
T7-e 钉过「腾讯 rows 顺序是 日期/开/收/高/低/量」，同类教训，这次是单位维度；
② 前复权不改成交量 ⇒ 两仓（前复权/不复权）同日 volume 比值 ≈1 是**无需第三方**的内部判据；
③ 第三方基准（Wind/交易所）对照是「演练 PASS」之外唯一能证数值正确性的手段。
**自动化路径**：① 机器判据 = `scripts/data_sync.py --content` 的 volume 交叉校验
（同日比值 ∉[0.2,5] 即判红）+ `tests/test_data_sync_content_check.py`（注入 ×100 自证判红）。

### P-123 · 本地仓文件日期降序存储：读路径 tail() 语义反转，消费方拿倒序帧

**现象**：全仓普查 5578 只 kline：**2825 降序 / 2753 升序**。对照脚本首轮用 `tail()`
取「最新两行」，误判 600519/000001/300750「数据停在 2025-11-12（过期一年）」——实际取到
的是**物理末行=最老行**，文件本身是新鲜的。运行时风险：`get_stock_hist_multi_source`
本地命中返回 `cached.tail(days)`——降序文件给消费方倒序帧/最老数据，指标计算若假设
升序则静默错（与 ×100 同级：不报错）。

**根因**：写仓路径多条不收口：`_merge_write` 有 `sort_values("date")`，但
`data_sync.sync_kline` 单源直写（`write_frame` 裸传源帧）等其它写方不排序——降序来自
源报文（腾讯透传帧本身倒序），落仓层不保证序。「多路写同一仓」只有口头约定没有机制。

**修法**（待 ADR-0036）：`local_store.write_frame` 内统一 `sort_values("date")` 收口
（一处改全仓受益）；存量 2825 只降序文件重写一遍。

**推广**：① 多路写同一仓的横向契约（排序/schema/单位）必须收口在**写入层**，不能约定在
调用方；② 对照/巡检脚本先 `sort_values("date")` 再取头尾，`tail()` 不是「最新」；
③ 「自给自足演练 PASS」证链路通，证不了顺序与数值正确（P-122 同证）。
**自动化路径**：① 机器判据 = `scripts/data_sync.py --content` 升序判据
（降序/乱序即判红）+ `tests/test_data_sync_content_check.py`（注入降序文件自证判红）。

### P-124 · 本地仓 schema 多源并存：厂商原始帧绕过归一化直落仓

**现象**：kline 仓 5 种列集并存（2826×8列腾讯透传 / 2649×6列标准 / 86×9列含
pct_chg+turnover / **17×原始厂商帧**——`stock_code/trade_time/振幅/涨跌额` 等中英列
混排）。落仓契约（ADR-0033/T7-g）只钉了「代码归一 + 复权分仓」，没钉「列归一」。

**根因**：写仓路径 ≥4 条（data_kline_web 标准帧 / 腾讯透传 / adata / akshare 风格帧）
各自直接 `write_frame`，落仓层无 schema 白名单——P-123 同根因（写方不收口）的列维度。

**修法**（待 ADR-0036）：`write_frame` 增加列集白名单（白名单外拒写或映射后写）；
存量 17 只按归一化重写。

**推广**：① 「归一化单一真源」（P-114 的 `normalize_code`）要覆盖**列**而不只**键**——
键归一 + 列放任 = 半套契约；② 新源接入验收（T7-e 双栈登记）应加一条
「落仓后读回列集 ∈ 白名单」。
**自动化路径**：① 机器判据 = `scripts/data_sync.py --content` schema 白名单判据
（白名单外列集即判红）+ `tests/test_data_sync_content_check.py`（注入厂商帧自证判红）。

---

## 十二、增量评审追加（2026-11-22）

### P-125 · 源栈成员与复权口径脱钩：两个「全绿判据」挡不住的静默污染族

**现象**：增量架构评审（docs/audit/architecture-review-delta-2026-11-22.md Δ5，v2 经
对抗复核扩容）发现三个同根因的复权口径洞，**两轮全仓评审（11-20 四路探查 + 11-22
对抗复核）的初稿都只抓到了最不活跃的一个**：① tencent 在 raw 栈被无条件
`adjust="qfq"`（`registry:172`，2026-11-19 引入）——新浪失败回落腾讯时前复权价写进
kline_raw 仓；② westock 在 raw 栈不带 fq（默认 qfq）——同上；③ ADJUSTED 栈里排着
raw-only 的 baostock/tushare/tdx/adata/tdx_local——腾讯失败时 raw 价写进 kline 仓，
raw 栈尾部排着 qfq-only 的 tdx_local_qfq——反向污染。当时 C16 断网演练 PASS、
`--content` 三判据全绿、Wind 对照逐值一致——**三把判据都绿，污染洞依然存在**。

**根因**：「源 × 复权口径 × 仓」三元组从未被绑定——源栈登记（T7-e）只登记了成员
与顺序，没登记每个源**能不能**产出该口径；复权意图（prefer_adjusted）在
`_fetch_from_source` 边界丢失，双口径源（腾讯/westock）按硬编码分支取数；写仓层
（P-123/P-124 收口处）只校验排序/列集，不校验价格口径。**判据盲区的根因**：
`--content` 的三个判据（升序/单位/列集）都是「形态学」判据，而口径污染是「数值
语义」级——两仓内容相同时 volume 比值≈1、升序、列集全合法。

**修法**（增量评审整改 D-4，2026-11-22）：① `SOURCE_ADJUST_CAPABILITY` 能力表 +
`_stack_for(prefer_adjusted)` 栈过滤——incapable 源不试不写（宁缺毋假，P-114 同语义）；
② 双口径源按意图传参：tencent raw=`adjust=""`、westock raw=`fq="bfq"`（评审初稿误写
`fq=""`，CLI 文档口径 bfq=不复权——**处方也要过 G3 实测**）；③ 显式 source 组合在
`_fetch_from_source` 入口能力守卫；④ `--content` 增补第 4 判据（kline/kline_raw 同日
close 比值恒定的标的占比 >55% 判红——**个别恒定无法逐码判定**：无除权合法态与污染
态在数据上同构，干净仓实测基线 36.5%（5507 对中 2007 只，000002/000006 三方实测
腾讯 raw==qfq==存 raw 确认合法），只有**批量**恒定才是污染特征）。

**推广**：① 评审初稿 vs 复核扩容的差距本身就是教训——**报告里「只点名一个洞」的
条目要查同族**（Δ5 初稿只写 westock，复核抓出 tencent 更靠前；本文作者接着抓出
ADJUSTED 栈的 raw-only 集群）；② 判据家族要覆盖「形态 + 数值语义」两个维度，
形态全绿 ≠ 数值正确（P-122 的「C16 断网演练 PASS 证不了数值正确」在口径维度复发）；
③ 处方里的具体参数（`fq=""`）不因来源权威而豁免实测——G3 对评审处方同样适用。
**自动化路径**：① 机器判据 = `--content` 第 4 判据（`ADJ_MASS_FAIL_RATIO=0.55`，
标定记录在 data_sync.py）+ `tests/test_raw_stack_adjust.py` 5 例（能力表钉死 / 栈过滤 /
双口径分支意图 / 显式源守卫）；② 上提 AGENTS.md T7-e（双栈登记加口径能力维度）。

---

### P-126 · westock 封装三个函数命令用错（quote 渠道失效 / hot 裸调用 / lhb 缺股票码），P-111「已复活」结论只覆盖 kline 一函数

**现象**（2026-11-22 核验六类数据）：端到端调用 `data_westock` 七个函数实测——
`kline/finance/asfund/technical` 正常；`get_quote` 返回空 dict（npm CLI 报
`命令 "quote" 在当前渠道不可用`）；`get_hot` 返回空帧（npm CLI `hot` 缺子命令）；
`get_lhb` 返回空帧（npm CLI `lhb` 不带股票码直接报错 `请提供股票代码`）。
P-111/P-112 只核过 kline 一函数，"westock 已复活"被误当成整封装可用。

**根因**：① npm CLI（`westock-data-clawhub@1.0.4`）命令集与 Go CLI（`westock`）不同，
封装层按 Go CLI 语义写了 `quote`/`hot`，却下发到 npm CLI；② `lhb` 上游签名要求
股票码，封装没传；③ 判据（conftest autouse 桩 `_run_westock` 返空串 + 单函数测试）
让这三个坏函数在 L1 全绿下静默——**桩把所有命令都变成"合法的空结果"**。

**修法**（2026-11-22）：① 双 CLI 通道——npm CLI 保留已验证生产命令（kline 复权口径
P-125 已钉死，不迁移），Go CLI 补齐 npm 不支持的 quote/news/report/notice/sector/
index/macro/fund north+south/etf/hot；② `get_quote` 改走 Go CLI；`get_hot` 走
`hot <kind>` 子命令；`get_lhb(code, date)` 的 code 设必填；③ 新增 9 个 Go CLI 能力
函数补齐六类数据；④ `get_index_constituent` 用独立 `_to_index_code`（沪深300=
sh000300，复用个股规则会错成 sz000300——**指数与个股代码规则不同**）。

**判据**：`tests/test_westock_six_categories.py` 20 例——每个函数下发 argv 首段钉死
（升级改命令即判红）+ 坏命令不回归（quote 不得再调 npm / hot 不得裸调用 / lhb 必带
code）+ Go CLI 缺失降级为空 + 指数码映射独立。

**推广**：① 第三方 CLI 封装的"已修活"结论必须按**函数**核，不能按**模块**——
conftest 的全桩 + 单函数判据 = 其余函数静默失效的温床；② 同一 skill 的 npm/Go 双
CLI 命令集不同，封装层必须显式分流，不能按一个 CLI 的 help 写另一个的调用；
③ 指数代码规则（000xxx→sh）≠ 个股（000xxx→sz），共用 mapper 是静默错的源头。

---

### P-127 · Markdown 表格解析用 `if c.strip()` 过滤空单元格，合法空字段行被静默整行丢弃

**现象**（2026-11-22 Go CLI 授权后端到端实测）：`get_quote` 原始 CLI 输出有 49 字段，
`_parse_markdown_table` 却返回空 DataFrame；`get_hot`/`get_news` 同样空。直接跑 CLI
命令数据正常——封装层解析吞掉了数据。

**根因**：`cols = [c for c in line.split('|') if c.strip()]` 过滤所有空字符串，而
`quote.price_currency=''`、`hot.status=''`、`news.summary=''` 是**合法空字段**。过滤后
列数（如 47）少于表头（49），`len(cols)==len(header)` 不成立 → 整行丢弃，且不报错。
与 P-112 同族：第三方 CLI 输出形态与解析假设静默失配。

**修法**：`_cells()` 按 `'|'` 原始切分、只 strip 首尾空段（Markdown 行首行尾的 `|`），
**保留中间空单元格**；列数不符的行记 warning 而不是静默吞。判据
`tests/test_westock_six_categories.py::test_markdown_table_keeps_empty_cells`
钉死空单元格保留行为。授权后 9 类能力端到端全返回真实数据。

**推广**：解析 Markdown/CSV 表格时，`if c.strip()` 这类"清理空值"的写法会改变列数
语义——空单元格是数据，不是噪音；列数不符必须显式告警，静默 skip 是数据黑洞。

---

### P-128 · CLI 单条输出含多张 Markdown 表，解析器当成一张 → 附表刷列数不符 warning

**现象**（2026-11-22 P-127 修复后端到端复验）：`get_etf('sh510050')` 主表正确解析（1×19），
但日志刷 10+ 条「表格行列数不符」warning——来自「资产配置」「详细分类」「基金经理履历」
等附表（列数 2/5/4，与主表 19 列不同）。

**根因**：`_parse_markdown_table` 收集**全文所有** `|` 行当一张表解析，假设「一次 CLI
输出 = 一张表」。但 Go CLI 的 `etf profile` 等富信息命令会在主表后附多张小表，由非 `|`
文本行（`**资产配置**` 等）分隔。跨表行混判 → 列数不符被当异常告警。

**修法**：按**连续 `|` 行块**切分表格（非 `|` 行 = 边界），新增 `table_index` 参数默认
取第 0 张主表；单表输出行为完全不变。判据 `test_markdown_table_splits_multiple_tables`
钉死多表切分 + 按需取附表 + 单表兼容。

**推广**：Markdown 表格解析要先确认「一次输出几张表」——富信息 CLI 常常主表+附表；
把多表当单表会在"列数校验"这个环节制造假警报（与 P-127 的空单元格同属"解析假设
与真实输出形态失配"家族，一个吞数据、一个刷噪音）。

---

### P-129 · CLI 批量混入弱源代码（北交所）时整批返回空表，外层按 batch 落盘标 done = 静默缺口

**现象**（2026-11-22 全市场拉取）：A股 K线 5580 只按 20 只/批拉完、279 个分片全 `.done`、
0 失败，但汇总发现只覆盖 4208 只，缺 1372 只（多为 bj920xxx 北交所）。52 个分片是
空表（只有索引列、无 code 列）——CLI 批量里含北交所票时整批返回空，被外层当成功落盘。

**根因**：① 第三方 CLI 的 batch 是**局部降级**（`status: partial`）甚至整批空，
但 stdout 是空 Markdown 表，退出码 0；② 脚本按 batch 落盘 + 标 done，没逐码校验
覆盖度——"批次成功" ≠ "批内每只都有数据"。与 P-121（批量层只出 4 条）同族的
批量层静默失效。单只重拉 `bj920000` 完全正常，证明是批量路径问题。

**修法**：`scripts/fetch_bulk_repair.py` 对缺失代码逐只重试，单只成功才落补齐分片；
不覆盖原批次。**推广判据**：批量拉取必须做"universe - 实际覆盖"对账，
 `.done` 只能标记"这一批调用过"，不能当"这一批数据齐"；弱源（北交所/次新股）要
单独抽检。全量拉取脚本应在收尾自动跑覆盖对账并报缺口数。

**数据规模参考**（A股首批）：quote 5580 行×49列 / kline 595万行×10列（2013-2026）
/ finance 20万行×87列 / technical 183万行×21列，597MB。

---

### P-130 · 全量拉取的任务编排三坑：tail 缓冲失明 / 批次 done≠数据齐 / 限频退避空转

**现象**（2026-11-23~24 全市场拉取）：① 后台任务用 `| tail -40`，日志全缓冲到进程
结束才可见，多次"查进度"看不到任何输出，无法判断活着还是空转；② 补拉脚本
`--all` 六类别串一个进程、每类别结束才一次性落盘，中途 kill 全丢，且限频退避
（30/60/120/240s 累乘）让进程看似存活、2h19m 零产出；③ 主拉取按 batch 标
`.done`，但 CLI 批量局部降级使 done≠数据齐（P-129）。

**根因**：三个独立设计缺陷叠加——输出通道（`tail` 缓冲）、落盘粒度（类别级而非
逐条）、失败策略（无限退避）。三者共同制造"进程在跑但不可观测、不可中断恢复"的黑盒。

**修法**：① 进度可观测 = 逐条落 jsonl（每码一行），按唯一 code 数统计；② 断点
粒度 = 逐条，重跑跳过已 ok 的 code；③ 限频只退避一次（40s），仍失败记入 failed
跳过、下轮重试，单只 CLI 超时 60s（旧 180s 拖成空转）。补拉从 2h19m 零产出
变为 30s 增长 590 行可观测推进。**推广**：长任务必须"可观测 + 可中断恢复 +
失败有界"三件套；`| tail`/`| head` 做后台日志 = 主动失明，重定向到文件 + 逐条落盘
才是正解；done 标记只能表示"调用过"，覆盖度必须独立对账（universe - 实际 code）。

**全量成果**：16152 只 × 4 类，1.8GB / 3562 parquet，覆盖对账见
`data/exports/bulk/coverage_report.csv`。美股冷门粉单 75 K线 + 150 财务数据源
真实无覆盖（`数据为空`/`未找到财报数据`），非 bug，列 `unavailable_codes.csv`。

---

### P-131 · 第三方 CLI 的复权参数（--fq qfq）静默不生效：全量 K线实为不复权价

**现象**（2026-11-24 全量数据抽样质检）：用 westock Go CLI 拉 16152 只 K线时
统一带 `--fq qfq`，但质检发现茅台/万科/格力各 2000 条全历史中，
`qfq` 与默认（不带 fq，应=不复权）**2000/2000 逐值相同，比值恒 1.0**；
`hfq` 则明显不同（茅台 hfq≈9499 vs qfq≈1367）。即 **`--fq qfq` 在该 CLI 上
完全不生效**，拉取的"前复权 K线"实际是不复权价。

**根因**：CLI 的 fq 参数只对 hfq 真正处理，qfq 分支被忽略（或未实现）——
与 P-112（表头语言静默失配）同族的"第三方参数无契约"问题。致命在于：
命令退出码 0、数据非空、字段齐全，任何"能取到数"的判据都发现不了。

**影响**：① 全量 K线 `data/exports/bulk/*_kline_*.parquet` 是**不复权价**，
跨除权日有跳空，不能直接用于回测（P-125 口径污染同族）；② 之前 P-112/P-125
修复的是 npm CLI 的 westock（那时 qfq 生效），Go CLI 是新通道，参数行为不同。

**修法/建议**：① 质检脚本 `scripts/audit_bulk_quality.py` 把 qfq 生效性列为硬判据
（qfq==raw 即判红）；② 需前复权口径时改用 hfq 后用"最新价锚定"反推 qfq，
或换用项目主源栈（腾讯 qfq，registry 已验证）；③ 这批 bulk K线仅可作
**不复权**用途（成交量、换手、资金流对照），回测前必须换源或补复权。

**推广**：第三方 CLI 的每个"修饰性参数"（复权/区间/排序）都必须做
"带参数 vs 不带参数"的差分取证（P-56 空判据同构：两侧相同=参数没生效）；
不能因 CLI 接受参数、返回成功就默认它生效。

**同批质检发现**：港股 OHLC 数据源异常行 21.3%（OHLC=0 占位）、美股 11.4%，
剔除后真实 OHLC 违反 港 2.48%（微小精度差）/ 美 0.009% / A股 0；
成交量单位 A股=手(比值≈100)、港美股=股(比值≈1)，符合预期；
日期无未来值、单调无重复、最新均 2026-11-23。

---

### P-132 · 第三方输出文本字段含未转义管道符，Markdown 表格解析整行丢弃

**现象**（2026-11-24 补热搜新闻）：`hot news` 100 条只解析出 94 条，6 条含
`|` 的标题（如「利好利空晚报|刚刚，监管再出手」）被整行丢弃——列数 11 ≠ 表头 10。

**根因**：CLI 输出标题里的 `|` 未转义，与 Markdown 分隔符冲突；解析器 P-127
的列数相等判据把溢出行当坏行静默跳过。与 P-127（空单元格被吞）同族的
"解析假设与真实输出形态失配"。

**修法**：`_parse_markdown_table` 对列数溢出且存在文本型列（title/news_title/
name/summary）的行，把溢出段并回该文本列（首尾列边界由 header 固定，可精确复原），
再校验列数相等。判据：含 `|` 标题行完整保留、普通行不变。
**注意**：这不是硬编码特定新闻，而是通用"文本列吞溢出"策略，任何含管道符的
文本字段都受益。

**推广**：Markdown 表格解析遇到"列数不符"先判断是缺列（P-127 空单元格）、
多表（P-128）、还是文本含分隔符（本文）——三种根因修法完全不同，
一律"跳过异常行"会静默丢数据。

---

### P-133 · 腾讯 fqkline 接口 WAF 封禁：全市场高频拉取前复权不可行，1050/5580 后整体 501

**现象**（2026-11-24 补前复权）：用 `get_tencent_kline(adjust='qfq')` 逐只拉 A股
500 条前复权，前 1050 只成功后，剩余 4530 只全部返回空；失败票重试仍空，且
**之前成功过的 600519 也变空**。直连接口返回
`waf.tencent.com/501page.html`（HTTP 501）——腾讯 WAF 触发封禁。

**根因**：腾讯 `web/ifzq.gtimg.cn/appstock/app/fqkline/get` 对高频请求有 WAF 限流，
连续拉 ~1000 只后整段 IP 被封；与具体代码、天数参数无关（500/250/100 全 501，
qfq/hfq/不复权全 501）。等待 30/60/120s 未恢复——非短时限流，是持续封禁。

**影响**：① 真前复权只拿到 1050 只（`hs_qfq_500.parquet`，48.8万行），
其余 4530 只短期无法经腾讯源补齐；② 不可用 hfq 数学反推代替（P-131：与基准
中位差 1.19%）；③ Go CLI 通道正常（授权后走不同端点），但 qfq 参数不生效（P-131）。

**建议**：① 全市场前复权需换源（akshare 东财 push2his 本机不可用、或 tushare
token、或降低腾讯请求速率分多日拉）；② 已完成 1050 只可先用于近端分析；
③ 腾讯源仅适合小批量、低频、按需拉取，不做全市场批量。

**推广**：第三方免费行情接口普遍有 WAF/速率限制——**批量拉取前先测极限速率
并设计冷却**，连续成功不等于可持续；失败模式要区分"票无数据"与"源被封"
（本例 600519 也失败即可判定后者），否则会误记 4530 只"无数据"。

---

### P-134 · 同一 skill 不同命令的批量输出结构不同，解析器按命令分别处理

**现象**（2026-11-24 补北向/南下）：`fund north-holding`/`south-holding` 批量拉全失败
（5580+2783），但单只命令数据正常。原因：脚本对 news/report/notice 用
`**code**` 分组解析、对 fund 用单表带 code 列解析，而 north 实际是**单表带 code 列**
（同 fund）、south 是 `**code name**` 分组（带名称）——都套错了解析器。

**根因**：同一 CLI 的批量输出有至少三种结构（分组标题 / 单表带 code / 带名称分组），
按"类别所属大类"猜解析方式是错的——必须按**实际命令的返回形态**分别处理。
与 P-129（批量降级）、P-132（管道符）同族："批量返回形态"没有统一契约。

**修法**：`fetch_market_extra.parse_grouped` 明确分三路：north 单表带 code、
south 单表优先否则按 `**code name**` 分组、news/report/notice 按 `**code**` 分组、
fund 单表带 code。每路都有实测样本判据（3 只批量返回记录数 + code 覆盖）。

**推广**：给第三方 CLI 写批量封装时，每个命令都要单独抓一次批量输出样本确认结构；
"同一 group 的命令格式应该一样"是错误假设（fund/north/south 同组但三种形态）。

---

### P-135 · 分片合并去重键按 code 选，把财务 40 期压成 1 期（数据静默丢失 99.97%）

**现象**（2026-11-25 整合全量数据）：`integrate_bulk.py` 合并 finance 分片后
主表只剩 5580 行（每只 1 行），而原始分片是 20 万行（每只 40 期）。

**根因**：去重逻辑 `drop_duplicates(subset=["code"])` 用 code 作唯一键——
对 quote（每只 1 行）正确，对 finance（每只多期）错误，把 40 期压成 1 期，
且无报错、行数看起来"合理"，是静默数据丢失。

**修法**：按类别自然主键去重——有 `date` 用 (code, date)，否则有 `EndDate`
用 (code, EndDate)，最后才退到 code。修后 finance 恢复 20 万行。

**推广**：分片合并的去重键必须按**数据粒度**选，不能用"通用键"；
合并后必须做"合并前行数 vs 合并后行数"对账——压掉 99.97% 却能无感通过，
说明缺行数守恒判据。

---

### P-136 · 清洗 OHLC=0 占位行的边界：61 只票全部历史仅 last 有效，整只被剔

**现象**（2026-11-25 用途数据集清洗）：按规则剔除 open/high/low≤0 的行后，
港股 2 只 + 美股 59 只票的**全部历史行**被剔光（整只消失），但它们有 7,919 行
有效收盘价（如 hk01863 last=1.37、hk00068 last=0.52）。港股清洗后代码数
2783→2781、美股 7714→7655。

**根因**：清洗规则按"行"剔除，未考虑"整只票只有 last 有效"的边界——数据源对
部分冷门票只返回收盘价、OHLC 全填 0。整行剔除对回测是正确（无完整 OHLC 不能算
指标），但对前端展示是过度（收盘价走势本可展示）。

**修法**：① 严格清洗版用于回测集（无完整 OHLC 不进）；② 明细落
`_clean/fully_dropped_codes.csv`，两份 README 标注"需展示这些票请用
_integrated 原始 last 列"；③ 不擅自用 last 填充 OHLC（会制造假开高低）。

**推广**：占位行清洗必须统计"清洗后消失的实体数"——只报行数守恒会漏掉
整只实体消失的边界；严格/宽松两套口径要分开，不可用一个规则通吃回测与展示。

---

### P-137 · 增量数据以 jsonl 为真源，下游却读整合时的旧快照 → 回测集滞后 265 只

**现象**（2026-11-25）：前复权增量从 2756 推进到 3021 只，重跑
`prepare_usecases.py` 生成回测集，却仍是 2756 只——读的是
`_integrated/hs_kline_qfq_500.parquet`（上次整合的静态快照），不是实时 jsonl。

**根因**：整合流程把增量 jsonl 固化成 parquet 快照，下游脚本误把快照当真源。
快照只在 `integrate_bulk.py` 运行时更新，而增量批次持续写入 jsonl——两者必然漂移。

**修法**：`prepare_usecases.py` 的回测集前复权部分改为直接读 `_qfq/hs_qfq.jsonl`
（实时真源），重跑后正确生成 3021 只 / 139.7 万行。`_integrated` 快照保留作
历史归档，不作为增量数据的下游输入。

**推广**：增量数据链路必须明确"真源"是哪个文件——静态快照只能用于归档/展示，
下游消费必须直连追加型日志（jsonl），否则每次增量都要重跑整合才能生效。

---

### P-138 · Git Bash 的 `ps aux | grep python` 看不到独立 worker，误判任务已死

**现象**（2026-11-25）：分时任务正在正常运行（日志 30 秒 +4692 行），但
`ps aux | grep -i python` 只显示一个 py.exe 启动器，看不到实际 worker；
更早时连 worker 的子进程 `westock.exe minute usOMAB --days 1` 也完全不可见。
曾据此误判"进程只剩1个、worker 可能已退出"。

**根因**：Git Bash 的 `ps` 只枚举当前 MSYS 会话的进程，独立启动或父进程不同的
Python/westock 进程不在其视野内——是**观测工具的能力边界**，不是进程不存在。

**修法**：`scripts/list_processes.ps1` 用 Windows 原生 `Get-CimInstance Win32_Process`
列全部 python/westock 进程，含 PID/PPID/父进程名/完整命令行/启动时间，支持 `-Pattern` 筛选。
实测能看到 worker 完整命令行及其 westock 子进程。

**推广**：Windows 下判断"进程是否活着"必须用 PowerShell CIM 或 tasklist，
不能用 Git Bash `ps`；**最可靠的存活判据仍是产物增长**（check_task_health.py 三要素），
进程列表只作辅助取证。

---

### P-139 · 路径硬编码散落 N 个脚本，目录重构需改 N 处 → 用路径真源模块根治

**现象**（2026-11-25 目录整理）：bulk 数据目录命名混乱（根目录散落 3573 个文件，
_extra/_extra2/_extra3/_tmp 语义不明），12 个脚本各自硬编码 `OUT / "_xxx"` 路径。
直接移动文件会让所有脚本失效——"整理目录"变成 N 处字符串替换的高风险改动。

**根因**：路径定义没有单一真源——每个脚本重复声明 `OUT = ROOT / "data/exports/bulk"`
及其子目录，重构必然多处改、易漏改。这正是"补丁加补丁"的温床。

**根治方案**：
1. `scripts/bulk_paths.py` 作为**路径唯一真源**，定义全部 13 个语义目录常量
   （01_raw_shards / 02_markers / ... / 12_reports）及关键文件；
2. `scripts/migrate_bulk_layout.py` 一次性迁移文件 + 重写脚本引用为 import bulk_paths；
3. 以后改目录只改 bulk_paths.py 一处。

**执行前置**：所有 bulk 后台任务必须停止（活跃写入路径迁移会断）——
migrate 脚本内置 activity_check 检测 minute.jsonl 是否还在增长，增长则拒绝执行。

**推广**：任何"多脚本共享同一目录树"的场景，路径必须收敛到单一模块；
目录名可以重构，但重构成本应恒定为"改 1 个文件"，而非"改 N 个脚本"。

---

### P-140 · 长任务写入中的目录重构：Junction 保留旧路径实现零中断迁移

**场景**（2026-11-25）：bulk 目录 3573 文件散落、10 个子目录命名混乱，需重构；
但分时任务正写 `_extra3/_logs/minute.jsonl`（逐只 `log.open("a")` 重新打开路径），
直接移动会让后续写入在旧路径重建文件、数据分裂。

**方案**：Windows `New-Item -ItemType Junction`——目录物理移到新语义目录后，
在旧路径建 Junction 指向新位置。运行中的任务写旧路径，实际落入新目录，零中断；
脚本用旧路径 glob/读取也照常工作（junction 对 pathlib 透明）。

**执行**：① 10 个目录 Move-Item + Junction（活跃 `_extra3` 也在内）；
② 根目录 3563 分片 → `01_raw_shards/`、universe → `03_universe/`、报告 → `12_reports/`；
③ 8 个脚本批量改 glob/路径为 import `bulk_paths.py`；④ 修 2 处自动替换误伤
（finalize 缺 sys.path、integrate 的 `_extra` glob 误改为 RAW）。

**验证**：活跃写入 30s +7038 行不中断；分片读取 280、universe 3、extra junction 19 文件；
全量判据 **1808 passed / 0 failed**。

**推广**：重构正在写入的目录树， Junction 是比"等任务跑完"更优的解——零停机、零改动
业务脚本、可随时回滚（删 junction 移回）；但文件级无法 junction，根目录散落文件必须
配合路径真源模块改脚本。

---

### P-141 · 脚本用 `parent.parent` 锚定仓库根：下沉一级后门禁静默检查错目录

**场景**（2026-11-25）：项目重构把 12 个门禁脚本从 `scripts/` 移入 `scripts/gates/`，
脚本内容未改；所有脚本均以 `ROOT = Path(__file__).resolve().parent.parent` 定位仓库根。

**症状**：`python scripts/gates/doc_structure_check.py` 报 7 项 FAIL（`缺少文件:
AGENTS.md / CLAUDE.md / RULES.md / README.md / docs/refactor/pitfalls.md`）；同一脚本
经旧路径 `python scripts/doc_structure_check.py`（硬链接仍在）运行则 PASS。
原因是 ROOT 被解析成 `D:/stock-scanner/scripts` 而非仓库根。

**根因**：根路径用「脚本自身所在层数」推导，脚本位置成了隐式契约；目录重构改变层数
即静默失效——不抛异常，只报"文件不存在"，极易被误读为文档缺失而非定位错误。

**修法（2026-11-25 已执行）**：ROOT 改为标记法自锚定——从 `__file__` 向上查找同时
含 `AGENTS.md` 与 `lib/` 的目录，找不到直接 `SystemExit` 报错；12 个门禁统一改，
原地写入保持硬链接（新旧路径同一 inode），LF/CRLF 按文件原样保留。
验证：① 12 个脚本经新/旧路径导入，`ROOT` 均为仓库根；② L2–L10 + doc/dep/coverage
共 11 项新旧路径对拍，退出码与输出逐行一致（L3 仅警告时间戳不同，已掩码比对）；
③ `compileall` 通过；④ 仓外运行副本得明确报错 `无法定位仓库根`（rc=1），不再静默错根。

**推广**：脚本定位仓库根不得依赖自身深度；用仓库标记文件自锚定，让"移动脚本"
不再改变语义。凡是"路径由位置推导"的写法，都要问一句：目录重构后它指向哪。

**路径收口（2026-11-25 同日）**：`scripts/gates/` 已纳入 git 跟踪；`AGENTS.md` §4 全部
门禁命令 + S6/S9（S9 按 ADR-0014 D2 标记退役）/§2.2 路由门禁/D7 判据均切到 `scripts/gates/`。
旧路径 `scripts/*.py` 作为同 inode 硬链接保留一个发版周期，期满后 `git rm` 旧名即可
（只删硬链接名，新路径文件仍在）。

---

### P-142 · 公开仓库：force-push 清不掉旧对象，events API 会把旧 SHA 暴露给所有人

**场景**（2026-11-26）：为让仓库公开且只显示可公开内容，用 filter-repo 重写历史
（替换密钥、剔除内部文档）后 force-push；refs 侧 gitleaks 0 leaks、新克隆验证通过，
随后把仓库转为 public。

**症状**：公开后匿名 API 仍能按旧 SHA 取到重写前的提交（含已剔除的密钥）；
仓库 events API 直接列出改写前的旧 tip SHA，等于公开了取旧历史的入口。

**根因**：force-push 只移动 ref，GitHub 仍保留不可达对象并支持按 SHA 读取；
历史里的敏感值不会因 ref 移动而消失。

**修法**：公开前不要在**原仓**上重写后强推；改为「删除原仓 → 新建空仓 → 推干净历史」，
从对象库层面不留旧提交。新仓首推后立即显式设默认分支——首个被推的分支会成为默认，
陈旧 feature 分支可能把旧树暴露成首页。

**判据**：① 匿名 `GET /repos/<owner>/<repo>/commits/<old_sha>` 必须 404/422；
② events API 不含改写前 SHA；③ 新克隆 gitleaks 0 leaks 且默认分支树 == 预期快照。

**推广**：任何"改写历史后公开"的需求，先问「旧对象会不会被按 SHA 拉走」；
能新建仓就不要在原仓强推。密钥一旦进过公开可达的对象库，按已泄露处理（轮换/废弃）。
