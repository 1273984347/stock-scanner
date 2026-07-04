#!/bin/bash
# R2 G finding fix: fail loudly if gitleaks binary is not in PATH
# Mirror of builderpulse/hooks/check-gitleaks-present.sh

set -e

if ! command -v gitleaks >/dev/null 2>&1; then
    cat >&2 << 'EOF'
ERROR: gitleaks binary not in PATH

Secret scanning cannot run. Install gitleaks:
  - Windows: winget install gitleaks.gitleaks
  - macOS:   brew install gitleaks
  - Linux:   https://github.com/gitleaks/gitleaks#installation

If you intentionally want to skip this check, use:
  git commit --no-verify
EOF
    exit 1
fi

echo "gitleaks found: $(command -v gitleaks) ($(${GITLEAKS_BIN:-gitleaks} version 2>&1 | head -1))"
exit 0