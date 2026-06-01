# -*- coding: utf-8 -*-
"""
Windows 矿工 (PC Worker)  beta-0.2
==================================
和安卓矿工同协议：register→pull→run→complete。除算力任务外，beta0.2 起支持：
  - "download" 并行预取：下载 url 并校验/测速，PC 以带宽 + 多核参与"装库/装插件"加速。
能力(caps)上报：cpu/hash/compute/download 恒有；python=有(本机能跑pip)；
build=本机能否编译安卓(检测到 gradle/gradlew 或 ANDROID_HOME 才为真)。

用法：
  .venv\\Scripts\\python.exe test_worker.py            # 跑一轮(最多10个任务)后退出
  .venv\\Scripts\\python.exe test_worker.py --loop      # 持续当矿工
"""
import json, time, urllib.request, hashlib, math, sys, io, os, shutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

WORKER_VERSION = "beta-0.2"   # 三端同步
BASE = os.environ.get("CC_URL", "http://127.0.0.1:9000")
DID = os.environ.get("CC_WORKER_ID", "pc-worker")


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def detect_caps():
    """探测本机能力。"""
    has_gradle = bool(shutil.which("gradle") or shutil.which("gradlew")
                      or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT"))
    return {"cpu": True, "hash": True, "compute": True, "download": True,
            "python": True, "build": has_gradle}


CAPS = detect_caps()
res = {"cpu": 10, "mem": 50, "mem_free_mb": 8000, "mem_total_mb": 32000, "cores": os.cpu_count() or 8,
       "battery": 100, "charging": True}
base = {"device_id": DID, "name": "Windows矿工", "kind": "pc", "net_session_mb": 0,
        "resources": res, "caps": CAPS, "ver": WORKER_VERSION}


def run_job(job):
    t = job["type"]; p = job.get("payload", {})
    if t == "prime":
        lim = p.get("limit", 100000); c = 0
        for k in range(2, lim + 1):
            isp = True; d = 2
            while d * d <= k:
                if k % d == 0: isp = False; break
                d += 1
            if isp: c += 1
        return {"primes": c, "limit": lim}
    if t == "hash":
        n = p.get("count", p.get("rounds", 100000)); h = str(p.get("seed", "s")).encode()
        for _ in range(n): h = hashlib.sha256(h).digest()
        return {"hash": h.hex()[:16], "rounds": n}
    if t == "compute":
        it = p.get("iterations", 1000000); acc = 0.0
        for i in range(it): acc += math.sqrt((i % 1000) + 1)
        return {"acc": acc, "iterations": it}
    if t == "download":
        # 【beta0.2】并行预取：下载 url、校验、测速，PC 贡献带宽参与装库/装插件加速。
        url = p.get("url", "")
        t0 = time.time(); size = 0; md = hashlib.sha256()
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                while True:
                    chunk = r.read(65536)
                    if not chunk: break
                    md.update(chunk); size += len(chunk)
            ms = (time.time() - t0) * 1000
            return {"url": url, "size": size, "sha256": md.hexdigest()[:16],
                    "ms": round(ms), "kbps": round(size / 1024 / max(0.001, ms / 1000), 1)}
        except Exception as e:
            return {"url": url, "error": str(e)}
    return {"echo": p.get("msg", "")}


def main():
    loop = "--loop" in sys.argv
    print("矿工 %s 注册 -> %s" % (WORKER_VERSION, post("/api/register", base)["level"]))
    print("能力 caps:", CAPS)
    done = 0; idle = 0
    while True:
        r = post("/api/pull_job", base)
        job = r.get("job")
        if not job:
            if r.get("paused"):
                print("被安全阈值暂停");
                if not loop: break
            if not loop:
                print("无更多任务"); break
            idle += 1
            time.sleep(2.0); continue
        idle = 0
        print("领取 %s (%s) ..." % (job["id"], job["type"]), end=" ")
        t0 = time.time()
        result = run_job(job)
        cpu_sec = (time.time() - t0) * res["cores"]
        post("/api/complete_job", {"device_id": DID, "job_id": job["id"], "result": result,
             "usage": {"cpu_core_sec": cpu_sec, "mem_mb_hour": 0.5, "net_mb": 0.05, "gpu_sec": 0}})
        print("完成 %s  (CPU %.1f核秒)" % (str(result)[:60], cpu_sec))
        done += 1
        if not loop and done >= 10:
            break
    print("\n本轮完成 %d 个任务" % done)


if __name__ == "__main__":
    main()
