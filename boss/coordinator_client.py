# -*- coding: utf-8 -*-
"""
协调端客户端 (Coordinator Client)
================================
老板不直接连手机——手机 worker 接在"协调端"(coordinator.py, 默认 :9000)上。
老板通过这个客户端：
  - status()        读协调端状态：有哪些设备在线、资源档位、记账。
  - submit(...)     把"手机那一份"任务提交进协调端队列，等手机 worker 来拉。
  - job_results()   轮询这些任务的完成情况、取回结果。

设计成"软依赖"：协调端没开也不报错，online() 返回 False，老板就只用本地 PC 干活。
"""
import requests


class CoordinatorClient:
    def __init__(self, base_url, timeout=5):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def online(self):
        try:
            requests.get(self.base + "/api/status", timeout=self.timeout)
            return True
        except Exception:
            return False

    def status(self):
        """返回协调端完整状态 dict；失败返回 None。"""
        try:
            r = requests.get(self.base + "/api/status", timeout=self.timeout)
            return r.json()
        except Exception:
            return None

    def online_workers(self, kinds=("phone",)):
        """返回在线的、且不是本机PC的 worker 列表（默认只看手机）。"""
        s = self.status()
        if not s:
            return []
        out = []
        for did, d in (s.get("devices") or {}).items():
            if not d.get("online"):
                continue
            if kinds and d.get("kind") not in kinds:
                continue
            out.append({"id": did, **d})
        return out

    def submit(self, jtype, payload, weight="normal", requires=None):
        """提交一个任务，返回 job_id（失败返回 None）。
        requires: 能力要求列表（如 ["download"]），协调端只把该任务派给具备能力的 worker。"""
        try:
            body = {"type": jtype, "payload": payload, "weight": weight}
            if requires:
                body["requires"] = requires
            r = requests.post(self.base + "/api/submit_job", json=body, timeout=self.timeout)
            return r.json().get("job_id")
        except Exception:
            return None

    def job_results(self, job_ids):
        """给一批 job_id，返回 {job_id: job_dict}（只含已知的）。"""
        s = self.status()
        if not s:
            return {}
        want = set(job_ids)
        return {j["id"]: j for j in (s.get("jobs") or []) if j.get("id") in want}
