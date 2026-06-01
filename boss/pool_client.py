# -*- coding: utf-8 -*-
"""
Offload SDK (算力池卸载客户端) · 全局模式核心
============================================
让【任意一端上的任意程序】把"很吃算力的活"丢进局域网算力池，由全体空闲设备一起算，
拿回结果——这就是"全局模式/一体机"在软件层的入口，也是"低端手机跑高端程序"的真实做法：
  弱机本地只发任务、收结果；真正的重计算在池子（PC+手机+模拟器）里并行完成。

用法（任意 Python 程序，任意一端，只要能连到协调端 :9000）：
    from pool_client import Pool
    pool = Pool("http://<协调端IP>:9000")
    # 单个卸载：把一个重任务交给池子，阻塞拿结果
    r = pool.offload("prime", {"lo": 2, "hi": 5_000_000})
    # 批量并行：一批任务撒给全池，并行算完按序返回（这才显著加速）
    rs = pool.map("compute", [{"iterations": 2_000_000} for _ in range(64)])
    # 装饰器：把一个纯函数标成"可卸载"（按 type 映射到池内已实现的负载）
    @pool.offloadable("prime")
    def count_primes(lo, hi): ...     # 调用即走池子

可卸载的任务类型 = 池内 worker 实现的：prime / hash / compute / montecarlo / download。
要加新类型：在三端 worker 的 runJob 里加同名实现即可（协议不变）。
"""
import time
import requests


class Pool:
    def __init__(self, url="http://127.0.0.1:9000", poll=0.4, timeout=180):
        self.url = url.rstrip("/")
        self.poll = poll
        self.timeout = timeout

    def online(self):
        try:
            requests.get(self.url + "/api/status", timeout=4)
            return True
        except Exception:
            return False

    def submit(self, jtype, payload, requires=None, weight="normal"):
        body = {"type": jtype, "payload": payload, "weight": weight}
        if requires:
            body["requires"] = requires
        r = requests.post(self.url + "/api/submit_job", json=body, timeout=10)
        return r.json().get("job_id")

    def _jobs(self, ids):
        ids = [i for i in ids if i]
        if not ids:
            return {}
        # 用 /api/jobs 按 id 查（不受 status 只回最近50条限制，多分片才不丢结果）
        r = requests.get(self.url + "/api/jobs", params={"ids": ",".join(ids)}, timeout=10).json()
        return r.get("jobs", {})

    def offload(self, jtype, payload, requires=None, weight="normal"):
        """提交一个任务并阻塞等结果。返回 result（超时抛 TimeoutError）。"""
        jid = self.submit(jtype, payload, requires, weight)
        if not jid:
            raise RuntimeError("提交失败：协调端不可用？")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            j = self._jobs([jid]).get(jid)
            if j and j.get("status") == "done":
                return j.get("result")
            time.sleep(self.poll)
        raise TimeoutError("任务 %s 超时" % jid)

    def map(self, jtype, items, requires=None, weight="small"):
        """把一批任务撒给全池并行算，按提交顺序返回结果列表（缺失为 None）。
        这是"增强并行计算/弱机调用池子"的主力接口。"""
        ids = []
        for it in items:
            ids.append(self.submit(jtype, it, requires, weight))
        out = {}
        pending = set(i for i in ids if i)
        deadline = time.time() + self.timeout
        while pending and time.time() < deadline:
            for jid, j in self._jobs(pending).items():
                if j.get("status") == "done":
                    out[jid] = {"result": j.get("result"), "worker": j.get("worker")}
                    pending.discard(jid)
            if pending:
                time.sleep(self.poll)
        return [out.get(i) for i in ids]

    def offloadable(self, jtype, requires=None):
        """装饰器：标记一个函数为"可卸载"。调用时把 kwargs 当 payload 发给池子。
        （函数体作为本地回退；池可用时优先走池。）"""
        def deco(fn):
            def wrapper(*args, **kwargs):
                if self.online():
                    try:
                        return self.offload(jtype, kwargs or (args[0] if args else {}), requires)
                    except Exception:
                        pass
                return fn(*args, **kwargs)   # 回退本地
            return wrapper
        return deco


if __name__ == "__main__":
    import sys
    pool = Pool(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9000")
    print("池在线:", pool.online())
    print("单个卸载 prime[2,1e6):", pool.offload("prime", {"lo": 2, "hi": 1_000_000}))
