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

### 漏洞报告模板（复制填写）

```text
标题: [组件] 一句话描述
报告人: <GitHub 账号（可选匿名）>
受影响版本/提交: <分支 + commit SHA 或 tag>
组件/文件: <例如 routers/scan.py、scripts/gates/contract_snapshot.py>
漏洞类型: <注入 / 越权 / 信息泄露 / DoS / 供应链 / 其他>
严重性自评: <Critical / High / Medium / Low + 理由>

复现步骤:
1. <前置条件>
2. <操作>
3. <观察到的结果>

最小 PoC:
<命令 / 请求 / 代码；请把 Token、Cookie、密钥替换为 <REDACTED>>

预期行为: <应当发生什么>
实际影响: <数据泄露 / 越权 / 拒绝服务 / 其他，影响谁>

环境:
- OS:
- Python:
- 依赖版本:
- 是否 public 仓已部署:

建议修复: <可选，给出可行方向>
披露偏好: 默认 90 天；其他请注明日期与理由
附件: <日志 / 截图 / 抓包，注意脱敏>
```

不要在实际报告里粘贴任何密钥、 Token 或他人隐私数据；需要证明泄露时只给脱敏片段。

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
