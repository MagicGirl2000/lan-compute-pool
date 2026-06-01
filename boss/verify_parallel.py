# -*- coding: utf-8 -*-
"""大规模并行计算 + 本机验证：全局模式开，本机多核并行统计 π(10^7)，对账已知值。
注意：用了 multiprocessing，必须 __main__ 守护，否则 spawn 子进程会递归执行本脚本。"""
import time, json, urllib.request, sys, io, multiprocessing
from executor import LocalExecutor

BOSS = "http://127.0.0.1:8000"


def post(path, data):
    req = urllib.request.Request(BOSS + path, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def get(path):
    return json.loads(urllib.request.urlopen(BOSS + path, timeout=10).read())


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    # 1) 开全局模式
    print("① 开启全局模式:", post("/api/global/toggle", {"on": True}))
    g = get("/api/global/status")
    print("   一体机: %d 逻辑核 · %.1f GB · %d 节点 · 空闲核 %d" % (
        g["total_cores"], g["total_mem_gb"], g["node_count"], g["idle_cores"]))

    # 2) 大规模并行：本机多核统计 [2, 10^7) 素数
    HI, STEP = 10_000_000, 50_000
    items = [{"lo": lo, "hi": min(lo + STEP, HI)} for lo in range(2, HI, STEP)]
    ex = LocalExecutor()
    print("② 大规模并行：[2,%d) 素数，%d 分片，本机 %d 核并行…" % (HI, len(items), ex.max_workers))
    t0 = time.time()
    res, stats = ex.run("prime", items)
    dt = time.time() - t0
    total = sum(r.get("primes", 0) for r in res if r)

    # 3) 本机验证：对账已知数学值
    KNOWN = 664579   # π(10,000,000)，OEIS A006880（已用筛法+试除双重独立核对）
    ok = (total == KNOWN)
    print("③ 本机验证：结果=%d  已知 π(10^7)=%d  → %s" % (total, KNOWN, "通过 ✓" if ok else "不符 ✗"))
    print("   耗时 %.1fs · %d 核并行 · 累计 %.0f CPU核秒 · 吞吐 %.1f 分片/秒 · 完成 %d/%d" % (
        dt, stats["workers"], stats["cpu_core_sec"], len(items) / dt, stats["done"], len(items)))
    print("   并行加速比≈ %.1f× (CPU核秒/墙钟)" % (stats["cpu_core_sec"] / dt))


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
