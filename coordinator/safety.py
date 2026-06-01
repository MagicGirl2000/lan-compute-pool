# -*- coding: utf-8 -*-
"""
安全阈值模块 (Safety Thresholds)
================================
分布式算力系统的"刹车"：任何设备(PC/手机)在贡献算力前，都要先过安全阈值检查。
超阈值 → 拒绝新任务 / 暂停 / 降速，保护设备不被算力租用拖垮、不过热、不耗尽电量。

阈值分三档：
  GREEN  正常       —— 可接全速任务
  YELLOW 接近上限   —— 只接小任务 / 降速
  RED    超阈值     —— 拒绝一切新任务，已有任务尽快收尾

PC 端用 psutil 读真实指标；手机端的阈值在 Android worker 里同构实现。
"""
import os

try:
    import psutil
except ImportError:
    psutil = None


# ── 默认阈值（可被环境变量 / data 覆盖）──────────────────────────────────────
DEFAULTS = {
    # CPU 占用率上限（%）。超过 RED 不再派活。
    "cpu_yellow": 75.0,
    "cpu_red":    90.0,
    # 内存占用率上限（%）
    "mem_yellow": 80.0,
    "mem_red":    92.0,
    # 磁盘占用率上限（%，针对租出去的盘）
    "disk_yellow": 85.0,
    "disk_red":    95.0,
    # 温度上限（℃，PC 多数无传感器时跳过；手机必查）
    "temp_yellow": 70.0,
    "temp_red":    82.0,
    # 电池下限（%，仅手机/笔记本）。低于 RED 停止贡献。
    "battery_red": 20.0,
    "battery_yellow": 35.0,
    # 是否要求充电中才贡献（手机强烈建议 True）
    "require_charging": False,
    # 网络流量上限（本次会话 MB；超了停网络类任务，防跑爆流量套餐）
    "net_session_mb_red": 5000.0,
    # 预留给系统的最小空闲内存（MB），低于此一律 RED。
    # 注：手机/小内存设备(如1GB模拟器)按绝对值1GB太苛刻，降到256MB；
    #   真正的内存保护交给 mem_red(占用率%) 这个相对指标。
    "min_free_mem_mb": 256.0,
}


def load_thresholds(overrides=None):
    t = dict(DEFAULTS)
    if overrides:
        t.update({k: v for k, v in overrides.items() if k in t})
    # 环境变量覆盖：SAFE_CPU_RED=85 等
    for k in t:
        env = os.environ.get("SAFE_" + k.upper())
        if env is not None:
            try:
                t[k] = type(t[k])(env) if not isinstance(t[k], bool) else env == "1"
            except Exception:
                pass
    return t


# ── 读取本机真实资源 ────────────────────────────────────────────────────────
def read_resources():
    """返回当前 PC 资源快照。手机端在 Android 侧用 BatteryManager/ActivityManager 同构。"""
    r = {"cpu": 0.0, "mem": 0.0, "mem_free_mb": 99999.0, "disk": 0.0,
         "temp": None, "battery": None, "charging": None,
         "cores": 1, "mem_total_mb": 0.0}
    if not psutil:
        return r
    try:
        r["cpu"] = psutil.cpu_percent(interval=0.3)
        r["cores"] = psutil.cpu_count() or 1
        vm = psutil.virtual_memory()
        r["mem"] = vm.percent
        r["mem_free_mb"] = round(vm.available / 1048576, 1)
        r["mem_total_mb"] = round(vm.total / 1048576, 1)
        # 系统盘占用
        du = psutil.disk_usage(os.path.abspath(os.sep))
        r["disk"] = du.percent
        # 温度（很多 PC 无此传感器）
        try:
            temps = psutil.sensors_temperatures()
            allt = [s.current for arr in temps.values() for s in arr if s.current]
            if allt:
                r["temp"] = round(max(allt), 1)
        except Exception:
            pass
        # 电池（笔记本有，台式无）
        try:
            bat = psutil.sensors_battery()
            if bat is not None:
                r["battery"] = round(bat.percent, 1)
                r["charging"] = bool(bat.power_plugged)
        except Exception:
            pass
    except Exception:
        pass
    return r


# ── 阈值判定 ────────────────────────────────────────────────────────────────
def evaluate(resources, thresholds, net_session_mb=0.0):
    """
    返回 (level, reasons)
      level ∈ {"GREEN","YELLOW","RED"}
      reasons = 触发原因列表
    """
    t = thresholds
    reasons = []
    level = "GREEN"

    def bump(new_level, why):
        nonlocal level
        order = {"GREEN": 0, "YELLOW": 1, "RED": 2}
        if order[new_level] > order[level]:
            level = new_level
        reasons.append(why)

    cpu = resources.get("cpu", 0)
    if cpu >= t["cpu_red"]:   bump("RED",    "CPU %.0f%% ≥ %.0f%%" % (cpu, t["cpu_red"]))
    elif cpu >= t["cpu_yellow"]: bump("YELLOW", "CPU %.0f%%" % cpu)

    mem = resources.get("mem", 0)
    if mem >= t["mem_red"]:   bump("RED",    "内存 %.0f%% ≥ %.0f%%" % (mem, t["mem_red"]))
    elif mem >= t["mem_yellow"]: bump("YELLOW", "内存 %.0f%%" % mem)

    free = resources.get("mem_free_mb", 99999)
    if free < t["min_free_mem_mb"]:
        bump("RED", "空闲内存仅 %.0fMB < %.0fMB" % (free, t["min_free_mem_mb"]))

    disk = resources.get("disk", 0)
    if disk >= t["disk_red"]:   bump("RED",    "磁盘 %.0f%% ≥ %.0f%%" % (disk, t["disk_red"]))
    elif disk >= t["disk_yellow"]: bump("YELLOW", "磁盘 %.0f%%" % disk)

    temp = resources.get("temp")
    if temp is not None:
        if temp >= t["temp_red"]:   bump("RED",    "温度 %.0f℃ ≥ %.0f℃" % (temp, t["temp_red"]))
        elif temp >= t["temp_yellow"]: bump("YELLOW", "温度 %.0f℃" % temp)

    bat = resources.get("battery")
    charging = resources.get("charging")
    if bat is not None:
        if bat <= t["battery_red"]:   bump("RED",    "电量 %.0f%% ≤ %.0f%%" % (bat, t["battery_red"]))
        elif bat <= t["battery_yellow"]: bump("YELLOW", "电量 %.0f%%" % bat)
    if t.get("require_charging") and charging is False:
        bump("RED", "未充电(策略要求充电中才贡献)")

    if net_session_mb >= t["net_session_mb_red"]:
        bump("RED", "本会话流量 %.0fMB ≥ %.0fMB" % (net_session_mb, t["net_session_mb_red"]))

    if not reasons:
        reasons.append("全部指标正常")
    return level, reasons


def can_accept_job(resources, thresholds, net_session_mb=0.0, job_weight="normal"):
    """
    是否可接新任务。
      job_weight: "small" | "normal" | "heavy"
      GREEN  接全部
      YELLOW 只接 small
      RED    一律拒绝
    """
    level, reasons = evaluate(resources, thresholds, net_session_mb)
    if level == "RED":
        return False, level, reasons
    if level == "YELLOW" and job_weight != "small":
        return False, level, reasons + ["YELLOW 档只接 small 任务"]
    return True, level, reasons


if __name__ == "__main__":
    th = load_thresholds()
    res = read_resources()
    lvl, why = evaluate(res, th)
    print("资源:", res)
    print("阈值档位:", lvl)
    print("原因:", "; ".join(why))
