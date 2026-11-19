"""带轮转的有界 JSONL 追加（评审 P1-3，2026-11-20）。

背景：``paper_signals.jsonl`` / ``frontend_errors.jsonl`` 此前无限 append——
磁盘满后所有写路径静默失败（audit 吞成 error、通知日志吞成 debug），系统的
表现为「数据静默停止落盘」而 API 仍返回 200，是最难察觉的一类失败。

轮转策略：文件超过 ``max_bytes`` → 当前文件 rename 为 ``<name>.1``（覆盖上一份
历史），继续写新文件。追加型低价值日志不做环形队列，保留 1 份历史即可。

失败语义：**轮转失败不阻塞写入**（降级为继续追加并 warning，与旧行为兼容）；
**写入失败照常上抛**——磁盘满必须让调用方可见，不在这里新增一层静默。
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def append_jsonl(path, record: dict, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    """带轮转的 JSONL 追加（单条 = 一行 JSON）。

    Args:
        path: 目标文件（str/Path，父目录不存在自动创建）。
        record: 可 JSON 序列化的单条记录。
        max_bytes: 轮转阈值；超过即把当前文件让位为 ``<name>.1``。
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > max_bytes:
            rotated = path.with_name(path.name + ".1")
            if rotated.exists():
                rotated.unlink()
            path.replace(rotated)
            logger.info("append_jsonl: %s 已轮转（超过 %d bytes）", path, max_bytes)
    except OSError as e:
        # 轮转是卫生措施，失败不阻塞写入；写入失败由下方 open 原样上抛
        logger.warning("append_jsonl: 轮转失败（继续追加）: %s", e)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
