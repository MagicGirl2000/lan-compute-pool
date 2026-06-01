# -*- coding: utf-8 -*-
"""
设备能力评估 (Capability Assessment)
===================================
老板分活的依据：每台设备到底有多强？本模块给每个设备打一个【算力评分】
(work-units/sec，越大越强)，再归一化成【分配比例】。

  - PC：本地真跑一段标准负载，用"单位时间完成多少标准 work-unit"当分数（吃满多核）。
  - 手机：往协调端丢一个【标定任务】，测它从领取到完成的耗时，反推分数（真实基准）；
          手机没连/没回，就按它上报的核数做保守估计。

得到分数后：ratio[device] = score[device] / Σscore。老板按这个比例切分项目工作量，
强的多分、弱的少分——这就是"按手机和电脑的算力能力比例分配任务"。
"""
import time

import workloads
from executor import LocalExecutor

# 一个"标准 work-unit"= compute 负载固定 iterations。PC/手机都用它做基准，可比。
CALIB_ITERS = 300_000


class CapabilityAssessor:
    def __init__(self, cfg, client):
        self.cfg = cfg
        self.client = client
        self._cache = {}          # device_id -> {"score":float,"detail":dict,"ts":float}
        self._pc_executor = LocalExecutor(cfg.get("pc_max_workers"))

    # ── PC 本地基准 ──────────────────────────────────────────────────────────
    def benchmark_pc(self):
        """本地吃满多核跑标定负载，返回 (score, detail)。score=work-units/sec。"""
        secs = float(self.cfg.get("benchmark_seconds", 1.2))
        workers = self._pc_executor.max_workers
        # 先粗估单 item 耗时，再凑够约 secs 的总活量分给多核
        t0 = time.time()
        workloads.w_compute({"iterations": CALIB_ITERS})
        single = max(1e-6, time.time() - t0)
        target_items = max(workers, int(secs / single * workers))
        items = [{"iterations": CALIB_ITERS}] * target_items
        t0 = time.time()
        _, stats = self._pc_executor.run("compute", items)
        wall = max(1e-6, time.time() - t0)
        score = target_items / wall            # work-units/sec
        return score, {"kind": "pc", "workers": workers, "items": target_items,
                       "wall_s": round(wall, 3), "single_item_s": round(single, 4)}

    # ── 手机基准（经协调端真实标定）──────────────────────────────────────────
    def benchmark_phone(self, device_id, wait_s=15.0):
        """
        往协调端丢一个标定 compute 任务，等这台设备完成，测耗时→分数。
        手机 worker 会自己来拉；若 wait_s 内没完成，退回按核数估计。
        """
        jid = self.client.submit("compute", {"iterations": CALIB_ITERS}, weight="small")
        if not jid:
            return self._estimate_phone(device_id), {"kind": "phone", "method": "estimate(no-coord)"}
        deadline = time.time() + wait_s
        t0 = time.time()
        while time.time() < deadline:
            jobs = self.client.job_results([jid])
            jb = jobs.get(jid)
            if jb and jb.get("status") == "done" and jb.get("worker") == device_id:
                wall = max(1e-6, time.time() - t0)
                return 1.0 / wall, {"kind": "phone", "method": "calibrated",
                                    "wall_s": round(wall, 3), "job": jid}
            # 别的设备把标定任务抢走了 → 重新投一个，继续等这台
            if jb and jb.get("status") == "done" and jb.get("worker") != device_id:
                jid = self.client.submit("compute", {"iterations": CALIB_ITERS}, weight="small")
            time.sleep(0.4)
        return self._estimate_phone(device_id), {"kind": "phone", "method": "estimate(timeout)"}

    def _estimate_phone(self, device_id):
        """拿不到真实基准时，用上报的核数 × 经验系数保守估手机分数。"""
        s = self.client.status() or {}
        d = (s.get("devices") or {}).get(device_id, {})
        cores = (d.get("resources") or {}).get("cores", 4)
        # 手机核普遍比 PC 核慢很多：每核约相当于 PC 标定的 ~0.6 work-units/sec
        return max(0.1, cores * 0.6)

    # ── 综合评估 + 比例 ────────────────────────────────────────────────────────
    def assess(self, force=False):
        """
        评估【本机PC + 所有在线手机】，返回:
          {
            "devices": {id: {"name","kind","score","ratio","detail","online"}},
            "ratio":   {id: 0~1},      # 归一化分配比例
            "total_score": float,
            "ts": epoch
          }
        带缓存（capability_ttl 内不重测）。
        """
        ttl = float(self.cfg.get("capability_ttl", 120))
        now = time.time()
        devices = {}

        # PC（除非显式关闭）
        if self.cfg.get("use_local_pc", True):
            c = self._cache.get("pc")
            if force or not c or now - c["ts"] > ttl:
                score, detail = self.benchmark_pc()
                c = {"score": score, "detail": detail, "ts": now}
                self._cache["pc"] = c
            devices["pc"] = {"name": "本机 PC", "kind": "pc", "online": True,
                             "score": c["score"], "detail": c["detail"]}

        # 在线手机
        for w in self.client.online_workers(kinds=("phone",)):
            did = w["id"]
            c = self._cache.get(did)
            if force or not c or now - c["ts"] > ttl:
                score, detail = self.benchmark_phone(did)
                c = {"score": score, "detail": detail, "ts": now}
                self._cache[did] = c
            devices[did] = {"name": w.get("name", did), "kind": "phone",
                            "online": True, "score": c["score"], "detail": c["detail"]}

        total = sum(d["score"] for d in devices.values()) or 1.0
        ratio = {k: d["score"] / total for k, d in devices.items()}
        for k in devices:
            devices[k]["ratio"] = ratio[k]
        return {"devices": devices, "ratio": ratio,
                "total_score": round(total, 3), "ts": now}
