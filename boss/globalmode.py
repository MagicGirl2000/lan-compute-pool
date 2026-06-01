# -*- coding: utf-8 -*-
"""
全局模式 / 一体机 (Global Mode) · beta0.2
========================================
把局域网里的 PC + 手机 + 模拟器，在软件层抽象成【一台机器】：
  - 统一资源视图：把全体在线设备的 核数/内存/GPU 合并成"一体机"总量。
  - 角色自动切换：谁在忙自己的事(安全阈值降级 / 进程CPU高 = 像在刷视频/编译)→标"忙(自用)"
    会自动少接活；谁空闲(GREEN)→标"闲(可助)"会去帮忙。
  - 双向互助：任何一端都既是 worker（帮别人算）又能 submit（把自己的重活交出去，
    见 pool_client.Pool）。协调端(:9000)是共享任务总线，本模块只做"统一视图 + 调度建议"。

★诚实边界：这是"可卸载/可并行任务"的一体化，不是 OS 层透明借用——
  刷视频的解码、任意原生程序的无感提速做不到；能做到的是把重计算/编译/下载丢进池子并行。
"""
import time


# 能效档位(用户可设 1-5，仿中国家电能效标签视觉)
# ★语义按用户校准：这是"给自己算力体设的挣钱档位"。
#   档位越高(5级)→ 火力全开、干活越多、挣工分越快，但耗电越多；1级=节能、挣得慢。
#   挣多少 = 干得多 × 交付准(准确性由真实工分的复算保证)。
#   设备能否净挣，还取决于它自身算力占比——弱机(如 WM)消耗>贡献，净工分可为负。
# 每级: (颜色, 标签, 描述)
_GRADES = {
    1: ("#00a651", "1级 · 节能", "省电优先 · 干活少 · 挣工分最慢"),
    2: ("#9acd32", "2级 · 低耗", "少量贡献 · 挣得较慢"),
    3: ("#f7e017", "3级 · 均衡", "性能与功耗平衡"),
    4: ("#f7941e", "4级 · 高速", "多核高载 · 挣工分快(耗电多)"),
    5: ("#ed1c24", "5级 · 满速", "火力全开 · 挣工分最快(耗电最多)"),
}


def level_to_cores(level, total):
    """能效档位 → 本机投入多少核干活。5级=几乎全核, 1级=1核。"""
    total = max(1, int(total or 1))
    frac = {1: 0.0, 2: 0.25, 3: 0.5, 4: 0.75, 5: 1.0}.get(int(level), 0.5)
    if level >= 5:
        return max(1, total - 1)
    return max(1, int(round(total * frac)) or 1)


class GlobalMode:
    def __init__(self, cfg, client, profile=None):
        self.cfg = cfg
        self.client = client
        self.profile = profile or {}
        self.on = bool(cfg.get("global_mode", False))
        self.history = []   # 吞吐/互助历史（给UI画趋势）

    def toggle(self, on=None):
        self.on = (not self.on) if on is None else bool(on)
        return self.on

    @staticmethod
    def _busy(d):
        """判断该节点是否在忙自己的事（→应被帮，而非帮别人）。"""
        r = d.get("resources", {}) or {}
        if d.get("level") and d["level"] != "GREEN":
            return True
        cpu = max(r.get("cpu") or 0, r.get("cpu_proc") or 0)
        return cpu >= 60

    def status(self):
        """一体机统一视图。"""
        s = self.client.status() or {}
        nodes = []
        tot_cores = tot_mem = tot_gpu = 0
        idle_cores = 0
        for did, d in (s.get("devices") or {}).items():
            if not d.get("online"):
                continue
            r = d.get("resources", {}) or {}
            cores = int(r.get("cores", 0) or 0)
            mem = float(r.get("mem_total_mb", 0) or 0)
            gpu = 1 if (d.get("caps", {}) or {}).get("gpu") else 0
            busy = self._busy(d)
            tot_cores += cores
            tot_mem += mem
            tot_gpu += gpu
            if not busy:
                idle_cores += cores
            nodes.append({
                "name": d.get("name", did), "kind": d.get("kind"),
                "cores": cores, "mem_gb": round(mem / 1024, 1),
                "level": d.get("level", "?"),
                "role": "忙·自用" if busy else "闲·可助",
                "busy": busy,
                "cpu": max(r.get("cpu") or 0, r.get("cpu_proc") or 0),
                "caps": d.get("caps", {}),
            })
        # 把本机 PC（老板所在机）也并进一体机总量（它不一定作为 worker 注册）
        pc = self.profile.get("cpu", {})
        local_cores = int(pc.get("logical", 0) or 0)
        local_mem = float(self.profile.get("mem", {}).get("total_mb", 0) or 0)
        local_gpu = len(self.profile.get("gpus", []))
        has_pc_node = any(n["kind"] == "pc" for n in nodes)
        if not has_pc_node and local_cores:
            tot_cores += local_cores
            tot_mem += local_mem
            tot_gpu += local_gpu
            nodes.insert(0, {"name": "本机 PC(老板)", "kind": "pc", "cores": local_cores,
                             "mem_gb": round(local_mem / 1024, 1), "level": "GREEN",
                             "role": "闲·可助", "busy": False, "cpu": 0,
                             "caps": {"python": True, "build": False}})
            idle_cores += local_cores
        st = {
            "on": self.on,
            "node_count": len(nodes),
            "total_cores": tot_cores,
            "total_mem_gb": round(tot_mem / 1024, 1),
            "total_gpu": tot_gpu,
            "idle_cores": idle_cores,
            "busy_nodes": sum(1 for n in nodes if n["busy"]),
            "nodes": nodes,
        }
        st.update(self._grade(st))
        return st

    def _grade(self, st):
        """能效档位(用户设的)+ 运行态。"""
        g = int(self.cfg.get("power_level", 3))
        g = min(5, max(1, g))
        color, label, desc = _GRADES[g]
        pc = self.profile.get("cpu", {})
        cores = level_to_cores(g, pc.get("logical", 0))
        # 运行态：全局模式开 + 有节点在忙(真在交付算力) = 全速运行中
        if not st["on"]:
            run, rcolor, full = "待命（全局模式未开）", "#8295ad", False
        elif st["node_count"] == 0:
            run, rcolor, full = "无设备接入", "#ed1c24", False
        elif st["busy_nodes"] > 0 or st["idle_cores"] < st["total_cores"]:
            run, rcolor, full = "⚡ 全速运行中", color, True
        else:
            run, rcolor, full = "就绪 · 待接活", "#42d392", False
        return {"grade": g, "grade_color": color, "grade_label": label, "grade_desc": desc,
                "level_cores": cores, "running": full, "run_state": run, "run_color": rcolor}
