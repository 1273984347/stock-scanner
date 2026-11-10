#!/usr/bin/env python3
"""RF-01 Phase 5 —— 把单体 api.py (3087 行) 按 17 域拆成 routers/ 包。

设计原则（详见 docs/adr/0005-phase5-api-split.md）：
  - 纯机械切分：用 AST 按行区间抽取，端点函数**原样迁移**，
    仅把装饰器 `@app.X` -> `@router.X` 改目标，路径/鉴权/签名/async 全保留。
  - 所有 import + 非路由 helper + 模块级状态收进 `routers/common.py`；
    各域模块 `from .common import *`（common 末尾用 `__all__` 显式导出下划线 helper）。
  - api.py 收窄成装配层：app 定义 + 中间件/异常/启动钩子/websocket + 17 个 include_router + 根路由。
  - 不改任何对外契约 => L2/L7 门禁应 0 漂移（拆分后需重跑 --capture 重生归属基线）。

判定（哪些留在 api.py，哪些进 common，哪些进域 router）：
  - 直接装饰 `@app.get/post/put/delete/patch/api_route` 且 path 在 ROUTERS 规划表里
    => 进对应域 router（装饰器改 @router.X）。
  - 直接装饰上述装饰器但 path 不在规划表（如根 `/`）=> 留在 api.py。
  - 装饰 `@app.exception_handler/middleware/on_event/websocket` => 留在 api.py（app 级）。
  - 普通函数/类/赋值/import/if 块：
      * 若块内含 `@app.`/`app.mount`/`app.include_router`/`uvicorn.run(app`/`__name__ == "__main__"`
        => 留在 api.py（静态挂载、入口、根路由等）。
      * 否则 => 进 common.py（helper / 状态 / import）。
      * 特例：`app = FastAPI(...)` 不进 common（api.py 自己定义真正的 app）。

行为保持（关键，不能静默漂移）：
  - `STATIC_DIR` / `_perf_file` / hs300 parquet 缓存均用 `Path(__file__).parent`，
    原 api.py 在仓库根 => 指向 根/static、根/cache。搬进 routers/common.py 后
    `__file__` 变成 routers/，必须统一锚定到仓库根 PROJECT_ROOT，否则静态/缓存落盘目录漂移。
  - 装饰器起点取 `min(node.lineno, 各装饰器 lineno)`：AST 中 FunctionDef.lineno 指向
    `def` 行而非装饰器行，漏掉会截断 `@app.xxx` 装饰器（端点/中间件全丢）。

运行：python scripts/split_api.py
（幂等：每次重跑都用 api.py 当前内容重新生成；生成后请跑 L0-L7 验证。）
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = ROOT / "api.py"
ROUTERS_DIR = ROOT / "routers"

# ---- 载入 ROUTERS 规划表（与 route_ownership_check.py 同一份权威来源）----
_spec = importlib.util.spec_from_file_location(
    "_roc", ROOT / "scripts" / "route_ownership_check.py")
_roc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_roc)
ROUTERS: dict[str, tuple[str, ...]] = _roc.ROUTERS

MOVE_DECORATORS = {"get", "post", "put", "delete", "patch", "api_route"}
APP_LEVEL_DECORATORS = {"exception_handler", "middleware", "on_event", "websocket"}

APP_IN_APIPY_RE = re.compile(
    r"@app\.|app\.mount|app\.include_router|app\.add_|uvicorn\.run\(app|"
    r"__name__\s*==\s*['\"]__main__['\"]"
)


def owner_of(path: str | None) -> str | None:
    if not path:
        return None
    for name, paths in ROUTERS.items():
        if path in paths:
            return name
    return None


def _decor_attr(dec) -> str | None:
    c = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(c, ast.Attribute):
        return c.attr
    return None


def _decor_target_is_app(dec) -> bool:
    c = dec.func if isinstance(dec, ast.Call) else dec
    return isinstance(c, ast.Attribute) and isinstance(c.value, ast.Name) and c.value.id == "app"


def _route_path(dec) -> str | None:
    if isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant):
        return dec.args[0].value
    return None


def _start_line(node) -> int:
    """装饰器起点：AST 中 FunctionDef.lineno 指向 def 行，装饰器在其上方。"""
    start = node.lineno
    for dec in getattr(node, "decorator_list", []):
        start = min(start, dec.lineno)
    return start


def _is_app_assign(node) -> bool:
    """`app = FastAPI(...)` 这类赋值：不进 common（api.py 自己定义真正的 app）。"""
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "app"
        and isinstance(node.value, ast.Call)
    )


def main() -> int:
    src = API.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    tree = ast.parse(src)

    route_blocks: dict[str, list[str]] = {}   # domain -> [source blocks]
    common_blocks: list[str] = []             # -> routers/common.py
    api_blocks: list[str] = []                # -> api.py (app 级，留在装配层)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            block = "".join(lines[_start_line(node) - 1:node.end_lineno])
            # 1) 直接装饰 app 路由装饰器？
            for dec in node.decorator_list:
                attr = _decor_attr(dec)
                if attr in MOVE_DECORATORS and _decor_target_is_app(dec):
                    path = _route_path(dec)
                    owner = owner_of(path)
                    if owner is not None:
                        moved = block.replace("@app.", "@router.", 1)
                        route_blocks.setdefault(owner, []).append(moved)
                    else:
                        api_blocks.append(block)  # 根路由等，留 api.py
                    break
            else:
                # 2) app 级装饰（异常/中间件/事件/websocket）？
                is_app_level = any(
                    _decor_attr(dec) in APP_LEVEL_DECORATORS and _decor_target_is_app(dec)
                    for dec in node.decorator_list
                )
                if is_app_level:
                    api_blocks.append(block)
                else:
                    # 3) 块内是否引用 app 装配（静态挂载 / 入口 / 嵌套路由）？
                    if APP_IN_APIPY_RE.search(block):
                        api_blocks.append(block)
                    else:
                        common_blocks.append(block)
            continue
        # import / assign / if / try / expr 等顶层语句
        if _is_app_assign(node):
            continue  # app = FastAPI(...) 由 api.py 自己定义，不进 common
        block = "".join(lines[node.lineno - 1:node.end_lineno])
        if APP_IN_APIPY_RE.search(block):
            api_blocks.append(block)
        else:
            common_blocks.append(block)

    # ---- 生成 routers/common.py（锚定仓库根，避免 __file__ 漂移）----
    common_py = (
        '"""RF-01 Phase 5 —— api.py 拆分的共享层。\n\n'
        "原 api.py 的全部 import、模块级状态、非路由 helper 集中在此；\n"
        "各域 router 通过 `from .common import *` 复用，末尾 `__all__` 显式导出下划线 helper。\n"
        '"""\n'
        "import pathlib\n"
        "# api.py 原在仓库根，STATIC_DIR/_perf_file/hs300 缓存均按根定位；\n"
        "# 本模块移至 routers/ 后必须以仓库根为锚，否则静态/缓存落盘目录漂移。\n"
        "PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent\n\n"
        + "\n\n".join(common_blocks)
        + "\n\n\n# 显式导出：含下划线 helper，使 `from .common import *` 在域模块中可用\n"
        '__all__ = [k for k in globals() if not k.startswith("__")]\n'
    )
    # 把 api.py 时代基于 __file__ 的路径统一换成仓库根锚点
    common_py = common_py.replace(
        "Path(__file__).parent", "PROJECT_ROOT")
    (ROUTERS_DIR / "common.py").write_text(common_py, encoding="utf-8")

    # ---- 生成各域 router ----
    for domain, paths in ROUTERS.items():
        blocks = route_blocks.get(domain, [])
        body = "\n\n".join(blocks)
        mod = (
            f'"""RF-01 Phase 5 域路由：{domain}"""\n\n'
            "from fastapi import APIRouter\n"
            "from .common import *  # noqa: F401,F403\n\n"
            "router = APIRouter()\n\n"
            f"{body}\n"
        )
        (ROUTERS_DIR / f"{domain}.py").write_text(mod, encoding="utf-8")

    # ---- 重写 api.py 为装配层 ----
    domains = list(ROUTERS.keys())
    # api.py 是顶层模块（被 `import api` / `python api.py` 直接导入），必须用绝对导入；
    # 域模块内部仍用 `from .common import *`（routers 是包）。
    import_line = "from routers import (\n    " + ",\n    ".join(domains) + ",\n)\n"
    includes = "\n".join(f"app.include_router({d}.router)" for d in domains)
    api_py = (
        '"""RF-01 Phase 5 —— API 装配层（原 api.py）。\n\n'
        "仅保留：app 定义、横切中间件/异常/启动钩子、WebSocket、根路由与静态挂载，\n"
        "以及 17 个域 router 的 include。所有端点逻辑已迁至 routers/ 各域模块。\n"
        '"""\n\n'
        "from routers.common import *  # noqa: F401,F403\n"
        + import_line
        + "\napp = FastAPI(title=\"A股量化交易系统\", version=\"3.0.0\")\n\n"
        + "\n\n".join(api_blocks)
        + "\n\n\n# ---- 域路由装配（原 api.py 的 68 个端点，按 17 域拆分）----\n"
        + includes
        + "\n"
    )
    API.write_text(api_py, encoding="utf-8")

    # ---- routers/__init__.py（空，避免隐式命名空间副作用）----
    (ROUTERS_DIR / "__init__.py").write_text("", encoding="utf-8")

    total_routes = sum(len(v) for v in route_blocks.values())
    print(f"[OK] split done: {len(domains)} routers, {total_routes} route functions moved")
    print(f"      common.py blocks={len(common_blocks)}  api.py blocks={len(api_blocks)}")
    for d in domains:
        print(f"      {d:16s} {len(route_blocks.get(d, [])):3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
