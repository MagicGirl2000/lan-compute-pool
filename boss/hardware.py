# -*- coding: utf-8 -*-
"""
PC 关键硬件探测与利用率 (Hardware Profiling)
==========================================
老板要"分配电脑所有关键硬件的分担任务"，先得知道这台 PC 有哪些关键硬件、
各自有多强、当前用了多少。本模块负责：

  profile()    —— 静态画像：CPU(核数/频率)、内存(总量)、磁盘(总量)、GPU(型号/显存)。
  utilization()—— 动态利用率：CPU% / 内存% / 磁盘% / GPU% / 网络收发，给 UI 实时刷。

GPU 探测是"尽力而为"：优先 pynvml(NVIDIA)，再试 nvidia-smi，都没有就标记无 GPU。
"""
import os
import shutil
import subprocess

try:
    import psutil
except ImportError:
    psutil = None

# Windows 上不弹控制台窗口（否则每秒调 nvidia-smi 会闪窗）
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ── GPU 探测 ──────────────────────────────────────────────────────────────────
def _gpu_via_pynvml():
    try:
        import pynvml
        pynvml.nvmlInit()
        n = pynvml.nvmlDeviceGetCount()
        gpus = []
        for i in range(n):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            gpus.append({"name": name, "mem_total_mb": round(mem.total / 1048576),
                         "index": i})
        pynvml.nvmlShutdown()
        return gpus
    except Exception:
        return None


def _gpu_via_smi():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.check_output(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL, timeout=4, creationflags=NO_WINDOW).decode("utf-8", "ignore")
        gpus = []
        for i, line in enumerate(l for l in out.splitlines() if l.strip()):
            parts = [p.strip() for p in line.split(",")]
            gpus.append({"name": parts[0],
                         "mem_total_mb": int(float(parts[1])) if len(parts) > 1 else 0,
                         "index": i})
        return gpus or None
    except Exception:
        return None


def detect_gpus():
    """返回 GPU 列表（可能为空）。"""
    return _gpu_via_pynvml() or _gpu_via_smi() or []


def gpu_utilization():
    """返回每块 GPU 的利用率% 与显存占用%（拿不到返回 []）。"""
    try:
        import pynvml
        pynvml.nvmlInit()
        out = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            u = pynvml.nvmlDeviceGetUtilizationRates(h)
            m = pynvml.nvmlDeviceGetMemoryInfo(h)
            out.append({"index": i, "gpu": u.gpu,
                        "mem": round(m.used / m.total * 100, 1) if m.total else 0})
        pynvml.nvmlShutdown()
        return out
    except Exception:
        pass
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        o = subprocess.check_output(
            [exe, "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"], stderr=subprocess.DEVNULL,
            timeout=4, creationflags=NO_WINDOW).decode("utf-8", "ignore")
        out = []
        for i, line in enumerate(l for l in o.splitlines() if l.strip()):
            a = [p.strip() for p in line.split(",")]
            used, tot = float(a[1]), float(a[2]) or 1
            out.append({"index": i, "gpu": float(a[0]),
                        "mem": round(used / tot * 100, 1)})
        return out
    except Exception:
        return []


# ── 静态画像 ──────────────────────────────────────────────────────────────────
def profile():
    """这台 PC 的关键硬件静态画像。"""
    p = {"cpu": {}, "mem": {}, "disk": {}, "gpus": detect_gpus()}
    if not psutil:
        return p
    try:
        freq = psutil.cpu_freq()
        p["cpu"] = {
            "logical": psutil.cpu_count(logical=True) or 1,
            "physical": psutil.cpu_count(logical=False) or 1,
            "freq_mhz": round(freq.max or freq.current) if freq else 0,
        }
        vm = psutil.virtual_memory()
        p["mem"] = {"total_mb": round(vm.total / 1048576)}
        du = psutil.disk_usage(os.path.abspath(os.sep))
        p["disk"] = {"total_gb": round(du.total / 1073741824, 1)}
    except Exception:
        pass
    return p


# ── 动态利用率 ────────────────────────────────────────────────────────────────
_last_net = {"t": None, "sent": 0, "recv": 0}


def utilization():
    """当前关键硬件利用率，供 UI 实时刷新。"""
    u = {"cpu": 0.0, "mem": 0.0, "disk": 0.0, "gpus": gpu_utilization(),
         "net_kbps_up": 0.0, "net_kbps_down": 0.0, "per_cpu": []}
    if not psutil:
        return u
    try:
        u["cpu"] = psutil.cpu_percent(interval=0.2)
        u["per_cpu"] = psutil.cpu_percent(percpu=True)
        u["mem"] = psutil.virtual_memory().percent
        u["disk"] = psutil.disk_usage(os.path.abspath(os.sep)).percent
        import time
        io = psutil.net_io_counters()
        now = time.time()
        if _last_net["t"] is not None:
            dt = max(0.001, now - _last_net["t"])
            u["net_kbps_up"] = round((io.bytes_sent - _last_net["sent"]) / dt / 1024, 1)
            u["net_kbps_down"] = round((io.bytes_recv - _last_net["recv"]) / dt / 1024, 1)
        _last_net.update({"t": now, "sent": io.bytes_sent, "recv": io.bytes_recv})
    except Exception:
        pass
    return u


if __name__ == "__main__":
    import json
    print("画像 / profile:")
    print(json.dumps(profile(), ensure_ascii=False, indent=2))
    print("\n利用率 / utilization:")
    print(json.dumps(utilization(), ensure_ascii=False, indent=2))
