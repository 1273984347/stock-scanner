"""文档分层自检的机器判据（AGENTS.md §7 D7）

背景：2026-11-20 文档分层重构（AGENTS.md / CLAUDE.md / RULES.md / README.md /
pitfalls.md 五文件分工）若无机器守卫，下次改文档就会重新长出「同一段规约抄在两个文件」
的老问题 —— 复制即漂移。本测试把「单一真源」变成可执行判据。

判据覆盖：
- 五个文件齐备，且各自含**自己专属**的锚点（防止内容被删空或职责错位）
- CLAUDE.md 引用 AGENTS.md（导入而非复制）
- 全仓活跃文档与源码注释里的 md 交叉引用**不断链**
- 跨文件无重复长段（同一段规约不得抄两遍）
- AGENTS.md 内规约 ID 唯一（D/B/S/T/I/P/C/G/RB 前缀）
- 判据自证：注入一条断链 ⇒ 必须判红

全程零网络、零副作用。
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SCRIPT = os.path.join(ROOT, "scripts", "doc_structure_check.py")


def _load():
    spec = importlib.util.spec_from_file_location("doc_structure_check", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load()


def test_five_files_exist_with_own_markers(mod):
    """五个文件齐备，且各自含专属锚点（职责错位/内容删空即判红）。"""
    failures = mod.check()
    assert failures == [], "文档分层判据未通过:\n" + "\n".join(failures)


def test_claude_imports_agents(mod):
    """CLAUDE.md 必须导入/引用 AGENTS.md（不是复制一份）。"""
    text = mod.read_text("CLAUDE.md")
    assert "@AGENTS.md" in text, "CLAUDE.md 缺 @AGENTS.md 导入语法"
    assert "AGENTS.md" in text


def test_agents_holds_global_rules(mod):
    """全局规约的唯一真源是 AGENTS.md（硬约束 + 门禁 + 规约 ID）。"""
    text = mod.read_text("AGENTS.md")
    for marker in ("硬约束", "L0", "L10", "回测调用规约", "文档与提交规约"):
        assert marker in text, f"AGENTS.md 缺 {marker!r}"


def test_rules_file_only_holds_exceptions(mod):
    """RULES.md 只管例外（例外登记 + 迁移对照 + 文档关系）。"""
    text = mod.read_text("RULES.md")
    assert "例外" in text
    assert "迁移对照表" in text
    # 全局规约不得再复制到 RULES.md
    for leak in ("### 1.3 `lib/` 子包职责与归属表", "## 四、验证规约"):
        assert leak not in text, f"RULES.md 仍复制了全局规约：{leak!r}"


def test_no_dangling_md_references(mod):
    """全仓活跃文档与源码注释里的 md 引用不得断链。"""
    failures = mod.check_references()
    assert failures == [], "存在断链引用:\n" + "\n".join(failures)


def test_no_duplicated_long_blocks(mod):
    """跨文件不得出现重复长段（同一段规约抄两遍 = 必然漂移）。"""
    failures = mod.check_duplicates()
    assert failures == [], "存在跨文件重复长段:\n" + "\n".join(failures)


def test_rule_ids_unique(mod):
    """AGENTS.md 内规约 ID 不得重复定义。"""
    failures = mod.check_rule_ids()
    assert failures == [], "规约 ID 重复:\n" + "\n".join(failures)


def test_gate_turns_red_on_injected_dangling_ref(mod, tmp_path, monkeypatch):
    """自证：注入一条指向不存在文件的引用 ⇒ 判据必须判红。"""
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    (tmp_path / "AGENTS.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (tmp_path / "RULES.md").write_text("例外\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "refactor").mkdir(parents=True)
    (tmp_path / "docs" / "refactor" / "pitfalls.md").write_text("规模\n", encoding="utf-8")
    # 注入核心文件的断链：行号越界（CLAUDE.md 只有 1 行）。
    # ⚠ 用拼接书写，避免判据扫到本测试文件时自指命中（那会让判据永远红）。
    bad = "CLAUDE.md:" + str(9999)
    (tmp_path / "broken.md").write_text(
        f"见 {bad} 与 no_such_file.md §1\n", encoding="utf-8")

    failures = mod.check_references()
    assert failures, "注入断链后判据仍绿 = 空判据"
    assert any(bad in f for f in failures), (
        f"未抓到核心文件的行号越界：{failures}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
