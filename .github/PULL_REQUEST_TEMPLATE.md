## 改动说明

<!-- 问题、方案、为什么这么做。大改动请先关联已对齐范围的 Issue。 -->

关联 Issue：<!-- #编号，无则填 无 -->

## 验证

<!-- 列出实际执行的命令与结果，不要只写「已测试」。参考 CONTRIBUTING.md： -->

```bash
python -m compileall -q lib scripts tests reporter backtest
python -m pytest tests/ -q --tb=line -p no:warnings -o log_cli=false
python scripts/gates/doc_structure_check.py --check
python scripts/gates/dep_direction_check.py --check
python scripts/gates/contract_snapshot.py --check
```

<!-- 涉及回测 / 评分 / 路由 / 阻塞 IO 时，按 AGENTS.md §4 补跑对应 L2–L10 门禁。 -->

门禁与测试结果：<!-- 粘贴关键输出 -->

## 口径影响

- 评分数值变化：<!-- 无 / 有（附 ADR 编号与对拍结果；变化 > 1e-9 必须走 ADR） -->
- 回测口径变化：<!-- 无 / 有（附对拍结果） -->

## 提交前自检

- [ ] 未提交 `.env`、密钥、数据导出、缓存与构建产物
- [ ] 描述与日志已脱敏，不含 Token / Cookie / 隐私数据
- [ ] 行为改动已同步 `README.md` / `AGENTS.md` / `CHANGELOG.md`
- [ ] CI 三平台（ubuntu / macos / windows × 3.12）全绿

> 安全漏洞不要用公开 PR 报告，见 [SECURITY.md](https://github.com/1273984347/stock-scanner/blob/master/SECURITY.md)。
