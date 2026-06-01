# -*- coding: utf-8 -*-
"""
看门狗 (Watchdog) —— "Claude 超时自动恢复"
==========================================
你要的需求：Claude/某进程 超过 90 秒无响应(无活动) → 自动做网络/系统恢复。

★ 安全设计（重要，别误解）：
  默认只做**安全恢复**(不会断你 VM 商店项目)：
    1. ipconfig /flushdns      刷新 DNS 缓存
    2. 重启本地 Flask 服务      (商店服务端 + 算力协调端，若挂了)
    3. 触发一次内存清理         (跑 清理内存.ps1)
  360 断网急救箱的"立即修复"**默认关闭**，因为它会重置网卡/Winsock，很可能
    断掉 VMware NAT(192.168.206.1) → 把 WM 商店项目搞断。
  只有你**明确**把 ENABLE_360_REPAIR=1 才会调 360，且仍优先安全恢复。

判定"无响应"的信号(可选其一)：
  - heartbeat 文件：被监控方每隔几秒 touch 一个文件；超 TIMEOUT 没更新 → 触发。
  - 网络探测：连续 ping 失败 → 触发(纯网络看门狗模式)。

运行：
  .venv\Scripts\python.exe watchdog.py
  set WD_TIMEOUT=90                # 无响应阈值秒(默认90=1分半)
  set WD_HEARTBEAT=D:\...\hb.txt   # 心跳文件(可选)
  set ENABLE_360_REPAIR=1          # 危险！允许调 360(默认0关闭)
  set WD_360_PATH="C:\...\360断网急救箱.exe"
"""
import os
import time
import subprocess
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = float(os.environ.get("WD_TIMEOUT", "90"))          # 秒
HEARTBEAT = os.environ.get("WD_HEARTBEAT", os.path.join(HERE, "_heartbeat.txt"))
PING_HOST = os.environ.get("WD_PING", "192.168.206.1")        # VM 网关，确认 VM 网络
ENABLE_360 = os.environ.get("ENABLE_360_REPAIR", "0") == "1"
PATH_360 = os.environ.get("WD_360_PATH", "")
CLEAN_PS1 = os.path.join(HERE, "清理内存.ps1")
LOG = os.path.join(HERE, "_watchdog.log")

# 要守护重启的本地服务（命令行特征 → 启动命令）
GUARD_SERVICES = [
    {
        "name": "商店服务端(app.py)",
        "match": "app.py",
        "port": 8080,
        "start": None,  # 商店服务端启动较特殊，仅告警不自动起(避免拉起错版本)
    },
    {
        "name": "算力协调端(coordinator.py)",
        "match": "coordinator.py",
        "port": 9000,
        "start": [os.path.join(HERE, ".venv", "Scripts", "python.exe"),
                  os.path.join(HERE, "coordinator.py")],
    },
]


def log(msg):
    line = "%s  %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def heartbeat_age():
    """心跳文件多久没更新(秒)。文件不存在返回 None(不触发)。"""
    if not os.path.exists(HEARTBEAT):
        return None
    return time.time() - os.path.getmtime(HEARTBEAT)


def ping_ok(host):
    try:
        r = subprocess.run(["ping", "-n", "1", "-w", "1000", host],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def port_listening(port):
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue).Count" % port],
            capture_output=True, text=True, timeout=8)
        return (r.stdout or "0").strip() not in ("", "0")
    except Exception:
        return False


# ── 恢复动作（从安全到激进）────────────────────────────────────────────────
def safe_recover(reason):
    log("⚠ 触发恢复：%s" % reason)
    # 1. flush DNS
    try:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, timeout=10)
        log("  ✓ flushdns")
    except Exception as e:
        log("  ✗ flushdns %s" % e)
    # 2. 清内存
    try:
        subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden",
                        "-ExecutionPolicy", "Bypass", "-File", CLEAN_PS1],
                       capture_output=True, timeout=30)
        log("  ✓ 内存清理")
    except Exception as e:
        log("  ✗ 内存清理 %s" % e)
    # 3. 守护服务：端口没监听就重启(仅有 start 的)
    for svc in GUARD_SERVICES:
        if not port_listening(svc["port"]):
            if svc.get("start"):
                try:
                    subprocess.Popen(svc["start"], cwd=HERE,
                                     creationflags=0x08000000)  # CREATE_NO_WINDOW
                    log("  ✓ 重启 %s" % svc["name"])
                except Exception as e:
                    log("  ✗ 重启 %s 失败 %s" % (svc["name"], e))
            else:
                log("  ! %s 端口%d 未监听(需手动起，避免拉起错版本)"
                    % (svc["name"], svc["port"]))


def repair_360():
    if not ENABLE_360:
        log("  · 360 修复已禁用(ENABLE_360_REPAIR!=1)。如确需，自行开启——"
            "但它会重置网卡，可能断 VM 商店连接！")
        return
    if not PATH_360 or not os.path.exists(PATH_360):
        log("  ✗ 未配置 360 路径(WD_360_PATH)")
        return
    try:
        subprocess.Popen([PATH_360])
        log("  ⚠ 已启动 360 断网急救箱(你已显式允许)。注意可能影响 VM 网络。")
    except Exception as e:
        log("  ✗ 启动 360 失败 %s" % e)


def main():
    log("=" * 50)
    log(" 看门狗启动  超时阈值=%.0fs  心跳=%s" % (TIMEOUT, HEARTBEAT))
    log(" 360修复=%s  ping目标=%s" % ("开(危险)" if ENABLE_360 else "关(安全默认)", PING_HOST))
    log("=" * 50)
    consecutive_net_fail = 0
    while True:
        try:
            triggered = False
            reason = ""
            # 信号1：心跳超时
            age = heartbeat_age()
            if age is not None and age > TIMEOUT:
                triggered = True
                reason = "心跳 %.0fs 未更新 (>%.0fs)" % (age, TIMEOUT)
            # 信号2：网络连续失败(纯网络看门狗)
            if not ping_ok(PING_HOST):
                consecutive_net_fail += 1
                if consecutive_net_fail >= 3:
                    triggered = True
                    reason = reason or ("ping %s 连续失败 %d 次" % (PING_HOST, consecutive_net_fail))
            else:
                consecutive_net_fail = 0

            if triggered:
                safe_recover(reason)
                # 安全恢复后再 ping，仍失败且允许了 360 才上 360
                time.sleep(3)
                if not ping_ok(PING_HOST) and ENABLE_360:
                    repair_360()
                # 触发后冷却，避免狂刷
                time.sleep(30)
            time.sleep(10)
        except KeyboardInterrupt:
            log("看门狗停止")
            break
        except Exception as e:
            log("看门狗异常: %s" % e)
            time.sleep(10)


if __name__ == "__main__":
    main()
