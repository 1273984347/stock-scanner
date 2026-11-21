"""本地数据仓 seed 包工具（ADR-0034）。

用法：
    python scripts/seed_pack.py pack   [--out dist/seed-20261121.tar.gz]
    python scripts/seed_pack.py check  <包路径>
    python scripts/seed_pack.py unpack <包路径> [--force]

设计（ADR-0033/0034）：
- data/store/ 二进制不入 git（99MB），只提交 manifest.json；
  seed 包作为「仓库自带数据」的分发载体，走 GitHub Release，不进版本库。
- 包内带 seed-meta.json（生成时间/文件清单/manifest SHA256），unpack 前校验完整性。
- unpack 默认**不覆盖**本地已存在且 mtime 更新的文件，避免回退数据；--force 强制覆盖。
- 排除 *.tmp / *.json.tmp（原子写残留）。
"""
import argparse
import hashlib
import json
import os
import pathlib
import sys
import tarfile
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
ROOT = pathlib.Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "store"
META_NAME = "seed-meta.json"


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _files() -> list:
    return sorted(
        p for p in STORE.rglob("*")
        if p.is_file() and not p.name.endswith(".tmp") and p.suffix != ".tmp"
    )


def cmd_pack(args) -> int:
    if not STORE.exists():
        print("本地仓不存在：data/store/"); return 1
    files = _files()
    if not files:
        print("本地仓为空，无需打包"); return 1
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    meta = {
        "schema_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "file_count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        "manifest_sha256": _sha256(STORE / "manifest.json") if (STORE / "manifest.json").exists() else None,
        "files": [str(p.relative_to(STORE)).replace("\\", "/") for p in files],
    }
    with tarfile.open(out, "w:gz", compresslevel=6) as tf:
        # meta 作为包内第一个成员
        meta_path = ROOT / "cache" / "seed-meta.tmp.json"
        meta_path.parent.mkdir(exist_ok=True)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tf.add(meta_path, arcname=META_NAME)
        meta_path.unlink(missing_ok=True)
        for p in files:
            tf.add(p, arcname=str(p.relative_to(STORE)).replace("\\", "/"))
    print(f"[pack] {out} | files={len(files)} | {out.stat().st_size/1e6:.1f}MB "
          f"(原始 {meta['total_bytes']/1e6:.1f}MB) | {meta['created_at']}")
    return 0


def _read_meta(tf: tarfile.TarFile) -> dict:
    try:
        f = tf.extractfile(META_NAME)
        if f is None:
            return {}
        return json.loads(f.read().decode("utf-8"))
    except (KeyError, ValueError, UnicodeDecodeError):
        return {}


def cmd_check(args) -> int:
    pkg = pathlib.Path(args.pkg)
    if not pkg.exists():
        print(f"包不存在：{pkg}"); return 1
    with tarfile.open(pkg, "r:gz") as tf:
        members = {m.name for m in tf.getmembers() if m.isfile()}
        meta = _read_meta(tf)
    if not meta:
        print("[FAIL] 包内缺 seed-meta.json 或解析失败"); return 1
    listed = set(meta.get("files", []))
    actual = members - {META_NAME}
    missing = listed - actual
    extra = actual - listed
    manifest_ok = None
    if meta.get("manifest_sha256"):
        with tarfile.open(pkg, "r:gz") as tf:
            try:
                data = tf.extractfile("manifest.json").read()
                manifest_ok = hashlib.sha256(data).hexdigest() == meta["manifest_sha256"]
            except (KeyError, AttributeError):
                manifest_ok = False
    ok = not missing and not extra and manifest_ok is not False
    print(f"[check] {'PASS' if ok else 'FAIL'} | files={len(actual)} "
          f"| 缺={len(missing)} 多={len(extra)} | manifest校验={manifest_ok} "
          f"| created={meta.get('created_at')}")
    if missing: print("  缺:", sorted(missing)[:5])
    if extra: print("  多:", sorted(extra)[:5])
    return 0 if ok else 1


def cmd_unpack(args) -> int:
    pkg = pathlib.Path(args.pkg)
    if not pkg.exists():
        print(f"包不存在：{pkg}"); return 1
    target_root = pathlib.Path(args.target).resolve() if getattr(args, "target", None) else STORE
    with tarfile.open(pkg, "r:gz") as tf:
        meta = _read_meta(tf)
        if not meta:
            print("[FAIL] 包内缺 seed-meta.json"); return 1
        members = [m for m in tf.getmembers()
                   if m.isfile() and m.name != META_NAME and ("/" not in m.name or not m.name.startswith(".."))]
        written = skipped = 0
        for m in members:
            target = target_root / m.name
            if target.exists() and not args.force:
                # 本地文件更新（mtime 更大）→ 不覆盖，避免数据回退
                if target.stat().st_mtime >= m.mtime:
                    skipped += 1
                    continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                continue
            with open(target, "wb") as f:
                f.write(src.read())
            written += 1
    print(f"[unpack] written={written} skipped={skipped} | 目标 {target_root}")
    print("[unpack] 提示：解包直写不入 manifest 账，建议跑 "
          "`python scripts/data_sync.py --verify --fix-manifest` 对齐账实（C15）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack"); p.add_argument("--out", default="dist/seed-latest.tar.gz")
    p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("check"); p.add_argument("pkg"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("unpack"); p.add_argument("pkg"); p.add_argument("--force", action="store_true")
    p.add_argument("--target", help="解包目标（默认 data/store/；测试可指定临时目录）")
    p.set_defaults(fn=cmd_unpack)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
