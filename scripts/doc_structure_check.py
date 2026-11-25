#!/usr/bin/env python
"""文档分层自检（判据，AGENTS.md §7 D7）

**为什么需要**：五文件分工（AGENTS.md 单一真源 / CLAUDE.md 导入层 / RULES.md 例外 /
README.md 人读入口 / pitfalls.md 经验）若无机器守卫，下次改文档就会重新长出
「同一段规约抄在两个文件」的老问题 —— 复制即漂移，用户会分不清哪条规则在生效。

检查项：
1. 五个文件齐备，且各自含**自己专属**的锚点（职责错位 / 内容删空即判红）
2. CLAUDE.md 引用 AGENTS.md（导入而非复制）
3. 交叉引用**不断链**，分级判定：
   - **FAIL**（判红）：核心五文件之间的引用 + 源码注释里的活引用
   - **WARN**（不判红）：历史取证面（docs/adr、docs/audit、docs/plans、docs/archive、
     TODO.md）对核心文件的**行号**引用 —— 那是当时的取证快照，改它等于篡改证据
   - 章节号引用一律按可解析判定（章节号是稳定引用，迁移对照表可追溯）
4. 跨文件无重复长段（>= MIN_DUP_LINES 连续相同非空行）
5. AGENTS.md 内规约 ID 唯一（D/B/S/T/I/P/C/G/RB 前缀）

用法：
    python scripts/doc_structure_check.py --check     # 判红即退出码 1
    python scripts/doc_structure_check.py --json      # 机器可读输出
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _resolve_repo_root(start):
    """自锚定仓库根：向上找同时含 AGENTS.md 与 lib/ 的目录，脚本可任意移动。"""
    for cand in (start, *start.parents):
        if (cand / "AGENTS.md").is_file() and (cand / "lib").is_dir():
            return cand
    raise SystemExit(f"[gate] 无法定位仓库根: 从 {start} 向上未找到 AGENTS.md + lib/")


ROOT = _resolve_repo_root(Path(__file__).resolve().parent)

#: 五个核心文件 + 各自**专属**锚点（缺一个即判红：内容被删空或职责错位）
FILES: dict[str, tuple[str, ...]] = {
    "AGENTS.md": ("硬约束", "L0", "L10", "回测调用规约", "文档与提交规约", "关键文件索引"),
    "CLAUDE.md": ("@AGENTS.md", "Claude Code 专属", "Skill"),
    "RULES.md": ("例外", "迁移对照表", "文档关系"),
    "README.md": ("快速开始", "目录结构", "数据源", "文档导航"),
    "docs/refactor/pitfalls.md": ("规模", "推广", "根因"),
}

#: 核心文件（相对仓库根）；用于分级判定
CORE: tuple[str, ...] = tuple(FILES)

#: 核心文件的 basename（用于识别裸引用，如 `pitfalls.md`）
CORE_BASENAMES = {"AGENTS.md", "CLAUDE.md", "RULES.md", "README.md", "pitfalls.md"}

#: 历史取证面：对核心文件的**行号**引用属当时快照，只告警不改写
HISTORY_PREFIXES = ("docs/adr/", "docs/audit/", "docs/plans/", "docs/archive/",
                    "docs/superpowers/", "docs/research/", "docs/README.md",
                    "TODO.md")

#: 扫描交叉引用的范围
SCAN_GLOBS = ("*.md", "docs/**/*.md", "scripts/**/*.py", "lib/**/*.py",
              "tests/**/*.py", "routers/**/*.py", "config/**/*.py",
              "reporter/**/*.py", "backtest/**/*.py")
EXCLUDE_PARTS = ("archive", "node_modules", "__pycache__", ".git")

#: 重复长段判定：连续相同非空行数阈值（代码块内的命令块不算重复）
MIN_DUP_LINES = 8

#: 规约 ID 前缀（AGENTS.md 内唯一性检查）
RULE_ID_RE = re.compile(
    r"^\|\s*(D\d+|RB\d+|S\d+|T\d+(?:-[a-z])?|I\d+|P\d+|C\d+|G\d+)\s*\|",
    re.MULTILINE)

#: md 引用：路径可带前缀、可带行号、可带章节号
_MD_REF_RE = re.compile(
    r"(?P<path>(?:[\w./-]+/)*[\w.-]+\.md)(?::(?P<line>\d+))?"
    r"(?:\s*§\s*(?P<sec>[\d.·]+))?")
#: markdown 链接形式 [text](path.md)
_MD_LINK_RE = re.compile(r"\]\((?P<path>[^)#]+\.md)(?:#[^)]*)?\)")

#: 占位符路径（模板示例，不判红）
_PLACEHOLDER_RE = re.compile(r"(YYYY|NNNN|XXXX|<[^>]+>)")


def read_text(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _scan_files() -> list[Path]:
    out: list[Path] = []
    for g in SCAN_GLOBS:
        for p in ROOT.glob(g):
            if any(part in EXCLUDE_PARTS for part in p.parts):
                continue
            if p.is_file() and p.suffix in (".md", ".py"):
                out.append(p)
    return sorted(set(out))


def _is_history(rel_src: str) -> bool:
    return rel_src.startswith(HISTORY_PREFIXES)


def _resolve(src: Path, path: str) -> Path | None:
    """把引用解析成仓库内文件；解析不到返回 None。"""
    if path.startswith(("/", "~")):
        return None                      # 仓外绝对路径（skill 目录 / 家目录）
    cand = (ROOT / path)
    if cand.exists():
        return cand
    alt = (src.parent / path).resolve()
    if alt.exists():
        return alt
    # 裸 basename → 核心文件唯一解析（如 \u0060pitfalls.md\u0060 → docs/refactor/pitfalls.md）
    name = Path(path).name
    if "/" not in path:
        matches = [ROOT / c for c in CORE if Path(c).name == name]
        if len(matches) == 1 and matches[0].exists():
            return matches[0]
    return None


def check_files() -> list[str]:
    """五个文件齐备 + 各自专属锚点。"""
    fails: list[str] = []
    for rel, markers in FILES.items():
        p = ROOT / rel
        if not p.exists():
            fails.append(f"缺少文件: {rel}")
            continue
        text = p.read_text(encoding="utf-8")
        for m in markers:
            if m not in text:
                fails.append(f"{rel}: 缺专属锚点 {m!r}（职责错位或内容被删空）")
    return fails


def check_imports() -> list[str]:
    """CLAUDE.md 必须导入 / 引用 AGENTS.md。"""
    text = read_text("CLAUDE.md")
    if not text:
        return ["CLAUDE.md 不存在"]
    if "@AGENTS.md" not in text:
        return ["CLAUDE.md 缺 @AGENTS.md 导入语法（AI 行为约束须唯一真源）"]
    return []


def check_references() -> list[str]:
    """交叉引用断链 —— 只返回**判红**项（历史取证面的行号引用只告警）。"""
    fails, warns = _scan_references()
    return fails


def check_references_warn() -> list[str]:
    """交叉引用断链 —— 只返回**告警**项（历史取证面 / 非核心目标）。"""
    _fails, warns = _scan_references()
    return warns


def _scan_references() -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []
    for src in _scan_files():
        try:
            text = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel_src = src.relative_to(ROOT).as_posix()
        candidates: list[tuple[str, int | None, str | None]] = []
        for m in _MD_REF_RE.finditer(text):
            candidates.append((m.group("path"),
                               int(m.group("line")) if m.group("line") else None,
                               m.group("sec")))
        for m in _MD_LINK_RE.finditer(text):
            candidates.append((m.group("path"), None, None))
        for path, line, sec in candidates:
            if _PLACEHOLDER_RE.search(path):
                continue                      # 模板占位路径
            target = _resolve(src, path)
            if target is None:
                msg = f"{rel_src}: 引用不存在的文件 {path!r}"
                if Path(path).name in CORE_BASENAMES and not _is_history(rel_src):
                    fails.append(msg)
                else:
                    warns.append(msg)
                continue
            try:
                rel_target = target.relative_to(ROOT).as_posix()
            except ValueError:
                continue
            is_core = rel_target in CORE
            try:
                body = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if line is not None:
                total = len(body.splitlines())
                if line > total:
                    msg = (f"{rel_src}: 引用 {path}:{line} 越界"
                           f"（该文件仅 {total} 行）")
                    # 历史取证面的行号引用 = 当时快照，只告警；核心文件之间的引用必判红
                    (warns if _is_history(rel_src) else fails).append(msg)
            if sec:
                key = sec.rstrip(".")
                has_heading = re.search(
                    r"(?m)^#{1,6}\s*[^\n]*" + re.escape(key) + r"(?![\d])", body)
                if not has_heading and key not in body:
                    msg = f"{rel_src}: 引用 {path} §{sec} 在目标文件中查无此章节"
                    (warns if _is_history(rel_src) else fails).append(msg)
    return fails, warns


def _content_blocks(text: str) -> list[str]:
    """切出「非代码块」的非空行（代码块内容不参与重复判定）。"""
    lines: list[str] = []
    in_code = False
    for raw in text.splitlines():
        if raw.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        s = raw.strip()
        if not s:
            continue
        lines.append(s)
    return lines


def check_duplicates() -> list[str]:
    """跨文件不得出现重复长段。"""
    blocks: dict[str, list[str]] = {}
    for rel in FILES:
        p = ROOT / rel
        if p.exists():
            blocks[rel] = _content_blocks(p.read_text(encoding="utf-8"))
    rels = list(blocks)
    fails: list[str] = []
    for i in range(len(rels)):
        for j in range(i + 1, len(rels)):
            a, b = blocks[rels[i]], blocks[rels[j]]
            sb = set(b)
            seen: set[int] = set()
            for idx, line in enumerate(a):
                if idx in seen or line not in sb:
                    continue
                window = a[idx:idx + MIN_DUP_LINES]
                if len(window) < MIN_DUP_LINES:
                    continue
                if all(w in sb for w in window):
                    fails.append(
                        f"{rels[i]} 与 {rels[j]} 存在 >={MIN_DUP_LINES} 行重复段落"
                        f"（首行：{window[0][:40]!r}）—— 单一真源原则：只在一处定义")
                    for k in range(idx, idx + MIN_DUP_LINES):
                        seen.add(k)
    return fails


def check_rule_ids() -> list[str]:
    """AGENTS.md 内规约 ID 唯一。"""
    text = read_text("AGENTS.md")
    if not text:
        return ["AGENTS.md 不存在"]
    ids = RULE_ID_RE.findall(text)
    dup = sorted({i for i in ids if ids.count(i) > 1})
    return [f"AGENTS.md 规约 ID 重复定义: {d}" for d in dup]


def check() -> list[str]:
    """全部检查项的失败清单（空列表 = 全绿）。"""
    fails: list[str] = []
    fails += check_files()
    fails += check_imports()
    fails += check_references()
    fails += check_duplicates()
    fails += check_rule_ids()
    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="判据模式（退出码 1 = 不通过）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    failures = check()
    warns = check_references_warn()
    if args.json:
        print(json.dumps({"ok": not failures, "failures": failures,
                          "warnings": warns},
                         ensure_ascii=False, indent=2))
        return 0 if not failures else 1

    if failures:
        print(f"[FAIL] doc structure: {len(failures)} problem(s)")
        for f in failures:
            print(f"      {f}")
        return 1
    print(f"[PASS] doc structure: {len(FILES)} files / 0 dangling ref / "
          f"0 duplicated block / rule ids unique"
          + (f" ({len(warns)} 历史面告警)" if warns else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
