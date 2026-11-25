# Security Policy

## Supported Versions

本项目以 `master` 为唯一维护线，安全修复只落在最新提交。

## Reporting a Vulnerability

请**不要**用公开 Issue 披露未修复的安全问题。

优先使用 GitHub 仓库的 **Security → Report a vulnerability**（私密 Security Advisory）；
若该入口不可用，请通过 GitHub 联系仓库所有者 [@1273984347](https://github.com/1273984347)。

报告请包含：

- 受影响的版本 / 提交
- 复现步骤与最小样例
- 影响范围（数据泄露、越权、拒绝服务等）
- 你希望的披露时间（默认 90 天）

## Scope

**接收**：本仓库代码、脚本、CI 配置、数据处理逻辑中的安全问题。

**不接收**：第三方数据源或依赖自身的漏洞（请向对应厂商报告）；投资建议或交易损失。

## Secrets

- 仓库不跟踪 `.env`；任何密钥、Token、Cookie 都不得提交。
- 一旦怀疑密钥进入过公开历史，按已泄露处理：先轮换/废弃，再清理历史。
- `pre-commit` 的 gitleaks 是最后一道防线，不是唯一防线。

## Response

维护者为个人开发者，尽力在 7 天内确认、30 天内给出修复或缓解方案；
不承诺固定 SLA，但会同步进展。
