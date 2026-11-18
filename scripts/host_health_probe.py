"""host 可达性探测（ADR-0025，2026-11-18）。

为什么需要：东财是「按 host 部分可用」——实测 push2 / push2his 不可用，
而 push2ex（涨停池/龙虎榜）正常。只测「东财能不能通」会得出相反结论，
进而把瞬时/局部故障当成整源不可用（坑表 P-99 的根因）。

用法：
    python scripts/host_health_probe.py            # 只报告，永远 exit 0
    python scripts/host_health_probe.py --strict   # 有 DEAD 则 exit 1（CI 用）

判据不是「HTTP 200」而是「响应体含该接口的业务标记」——CDN/网关常返回
200 + 空体或错误页。
"""
import argparse
import sys
import time

import requests

PROBES = [
    ("push2   (行情/资金流)", "https://push2.eastmoney.com/api/qt/clist/get"
     "?pn=1&pz=2&fs=m:1+t:2&fields=f12,f14", '"data"'),
    ("push2his(K线)", "https://push2his.eastmoney.com/api/qt/stock/kline/get"
     "?secid=1.600519&fields1=f1&fields2=f51,f53&klt=101&fqt=1&lmt=2", '"data"'),
    ("push2ex (涨停/龙虎榜)", "https://push2ex.eastmoney.com/getTopicZTPool"
     "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=5"
     "&sort=fbt%3Aasc&date=" + time.strftime("%Y%m%d"), '"data"'),
    ("emweb   (F10财务)", "https://emweb.securities.eastmoney.com/PC_HSF10/"
     "NewFinanceAnalysis/ZYZBAjaxNew?type=0&code=SH600519", "pages"),
    ("tencent (行情)", "http://qt.gtimg.cn/q=sh600519", "~"),
    ("sina    (行情)", "http://hq.sinajs.cn/list=sh600519", "hq_str_"),
    ("sina    (全市场)", "https://vip.stock.finance.sina.com.cn/quotes_service/"
     "api/json_v2.php/Market_Center.getHQNodeData?page=1&num=2&sort=symbol&asc=1&node=hs_a",
     "symbol"),
]

HEADERS = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}


def probe(url: str, marker: str, timeout: float = 8.0) -> tuple:
    t0 = time.time()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        body = resp.text or ""
        ok = resp.status_code == 200 and marker in body
        return ok, resp.status_code, len(body), time.time() - t0, "" if ok else f"缺标记 {marker!r}"
    except Exception as e:
        return False, 0, 0, time.time() - t0, f"{type(e).__name__}: {str(e)[:60]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true", help="有 DEAD 时退出码 1")
    args = ap.parse_args()

    print(f"{'接口':<24}{'结果':<6}{'HTTP':<6}{'字节':<9}{'耗时':<8}备注")
    print("-" * 78)
    dead = 0
    for name, url, marker in PROBES:
        ok, code, size, elapsed, note = probe(url, marker)
        if not ok:
            dead += 1
        print(f"{name:<24}{'OK' if ok else 'DEAD':<6}{code:<6}{size:<9}{elapsed:>5.1f}s  {note}")
    print("-" * 78)
    print(f"存活 {len(PROBES) - dead}/{len(PROBES)}；DEAD 只代表该 host 不可用，"
          f"不代表整个数据源不可用（见 ADR-0025）")
    return 1 if (args.strict and dead) else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
