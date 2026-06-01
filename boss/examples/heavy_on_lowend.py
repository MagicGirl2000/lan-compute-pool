# -*- coding: utf-8 -*-
"""
示例：弱机用算力池跑"高端"重计算 (Heavy compute on a low-end device via the pool)
==============================================================================
这个脚本本身几乎不耗算力——它把一个很重的素数统计任务【切片撒给整个池子】
(PC+手机+模拟器一起算)，再汇总。即使在很弱的机器上运行本脚本，真正的算力
来自池子，这就是"全局模式让低端设备调用高端算力"的真实形态。

运行（任意一端，改成你的协调端 IP）：
    python heavy_on_lowend.py http://192.168.1.20:9000
"""
import sys
import time

sys.path.insert(0, "..")
from pool_client import Pool


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9000"
    pool = Pool(url, timeout=300)
    if not pool.online():
        print("协调端不在线：", url); return

    # 把 [2, 6_000_000) 切成 60 片，撒给全池并行统计素数
    hi, step = 6_000_000, 100_000
    items = [{"lo": lo, "hi": min(lo + step, hi)} for lo in range(2, hi, step)]
    print("提交 %d 个分片到算力池，并行统计 [2,%d) 的素数…" % (len(items), hi))

    t0 = time.time()
    results = pool.map("prime", items, requires=["cpu"])
    dt = time.time() - t0

    total = sum((r["result"]["primes"] for r in results if r and r.get("result")), 0)
    by = {}
    for r in results:
        if r:
            by[r["worker"]] = by.get(r["worker"], 0) + 1
    print("素数总数 = %d" % total)
    print("耗时 %.1fs，分片由这些设备分担：" % dt)
    for w, c in by.items():
        print("   %-24s %d 片" % (w, c))
    print("（本机几乎没算——算力来自池子。这就是弱机调用池子算力。）")


if __name__ == "__main__":
    main()
