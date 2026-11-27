#!/bin/sh
# Conventional Commits 本地校验钩子（commit-msg stage）
# 零外部依赖：仅用 grep + sed，正则与 Conventional Commits 1.0.0 对齐。
#
# 校验规则：
#   首行必须匹配 type(scope)!: subject
#     type   = feat | fix | docs | chore | refactor | test | perf | ci | build | style | revert
#     scope  = 可选，如 (data) / (scripts)
#     !      = 可选，破坏性变更标记
#     subject = 非空
#   例外：Merge / Revert 自动生成的提交信息放行。
#
# 与项目现状对齐：近 100 条提交 100/100 已符合本规则（2026-11-28 审计），
# 本钩子把既有习惯机制化，不改变现有提交风格。

set -eu

MSG_FILE="${1:-}"
if [ -z "$MSG_FILE" ] || [ ! -f "$MSG_FILE" ]; then
    echo "[conventional-commit] 无法读取提交信息文件" >&2
    exit 1
fi

FIRST_LINE=$(sed -n '1p' "$MSG_FILE")

# 自动生成的提交放行
case "$FIRST_LINE" in
    Merge\ *|Revert\ *|revert:\ *) exit 0 ;;
esac

PATTERN='^(feat|fix|docs|chore|refactor|test|perf|ci|build|style|revert)(\([a-zA-Z0-9_-]+\))?!?: .+'

if printf '%s' "$FIRST_LINE" | grep -Eq "$PATTERN"; then
    exit 0
fi

cat >&2 <<EOF
[conventional-commit] 提交信息不符合规范：
  $FIRST_LINE

要求格式：type(scope)!: subject
  type: feat | fix | docs | chore | refactor | test | perf | ci | build | style | revert
  scope: 可选，如 (data)、(scripts)
  !:     可选，标记破坏性变更
示例：
  feat(data): blocks 新股覆盖判据落地（P-147）
  fix(api): 修复 WS 鉴权 token 泄漏
  docs: 补充 README 验证矩阵声明

例外：Merge / Revert 自动生成的提交信息自动放行。
EOF
exit 1
