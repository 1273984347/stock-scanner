"""环境与密钥域 —— .env 解析 + env 派生常量 + 调用期访问器（config 拆包 C11，2026-11-20）

自 config.py 下沉。⚠️ 路径基准（RULES §2.2）：本文件在 ``config/`` 包内，
仓库根 = ``Path(__file__).resolve().parent.parent``（2 级 parent，与 lib/<pkg> 的
3 级不同——每挪一级增减一级）。

.env 解析三条护栏（批次 C / P2-14，护栏文本为批次 C 原文）：
  1. key 白名单 `^[A-Z_][A-Z0-9_]*$`：空 key 不再抛 ValueError 让 `import config`
     整体崩掉，也挡住带空格/特殊字符的键；
  2. 值处理：成对引号去掉引号；未加引号时截断行内注释（`KEY=xxx # 备注`
     以前会把 `# 备注` 一起当值，鉴权必然失败）；
  3. `export ` 前缀与空行/纯注释行跳过。
"""
import os
import re
from pathlib import Path

# key 白名单
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _parse_env_value(raw: str) -> str:
    v = raw.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    idx = v.find(" #")
    if idx >= 0:
        v = v[:idx]
    return v.rstrip()


# 自动加载 .env 文件（模块级执行——config 被 import 时 env 即就位）
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k = _k.strip()
        if _k.startswith("export "):
            _k = _k[len("export "):].strip()
        if not _ENV_KEY_RE.match(_k):
            continue
        os.environ.setdefault(_k, _parse_env_value(_v))


# 飞书应用配置（优先读环境变量，未配置则留空）
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_RECEIVE_ID = os.environ.get("FEISHU_RECEIVE_ID", "")
FEISHU_RECEIVE_ID_TYPE = os.environ.get("FEISHU_RECEIVE_ID_TYPE", "open_id")

# TDX（通达信）数据源配置 —— lib/ 内一律从本模块取值，禁止绕过重复 os.environ.get
TDX_DATA_PATH = os.environ.get("TDX_DATA_PATH", "D:/TDX/vipdoc")
TDX_HOST = os.environ.get("TDX_HOST", "180.153.39.210")
TDX_PORT = int(os.environ.get("TDX_PORT", "7709"))
TDX_REF_PATH = os.environ.get("TDX_REF_PATH", "D:/TDX/vipdoc/ref")

# 告警推送失败降级目录（环境变量可覆盖，便于测试/部署）
ALERT_FALLBACK_DIR = os.environ.get(
    "ALERT_FALLBACK_DIR",
    str(Path(__file__).resolve().parent.parent / "logs" / "alert_fallback"),
)

# A 股年化交易天数（原 lib/ 内 4 处重复定义 + 3 处裸 252 年化字面量的单一真相源）
TRADING_DAYS_PER_YEAR = 252


def api_secret_key() -> str:
    """调用时读取（保持 monkeypatch.setenv 语义），env 名归 config 所有。"""
    return os.getenv("API_SECRET_KEY", "")


def tushare_token() -> str:
    """调用时读取（tushare pro api 懒初始化时取 token）。"""
    return os.environ.get("TUSHARE_TOKEN", "")


def stock_font_path_override() -> str:
    """调用时读取；返回字体覆盖路径（空串 = 无覆盖）。"""
    return os.environ.get("STOCK_FONT_PATH", "").strip()
