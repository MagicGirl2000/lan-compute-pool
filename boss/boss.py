# -*- coding: utf-8 -*-
"""
算力老板 (Compute Boss) —— 主程序 / Web 控制台
=============================================
"工人"是安卓算力 app 和 Windows 算力端(都接在协调端 coordinator.py 上)。
本程序是它们的"老板"：评估手机/电脑的算力比例，按比例把项目(含本地模型推理)
切分、分配、调度到电脑全部关键硬件 + 手机，并提供可视化控制台。

层次：
    [安卓 worker] [PC worker] ──► 协调端 :9000 ◄── 老板(本程序 :8000) ──► PC 本地多核引擎
                                      ▲                    │
                                      └─ 老板提交"手机那份"任务 ─┘  老板自己跑"电脑那份"

运行：
    .venv\\Scripts\\python.exe boss.py     # 控制台 http://127.0.0.1:8000
"""
import os
import socket
from flask import Flask, request, jsonify, render_template

import config
import hardware
from coordinator_client import CoordinatorClient
from capability import CapabilityAssessor
from executor import LocalExecutor
import projects as projects_mod
from scheduler import Scheduler
from devtasks import DevAccelerator

CFG = config.load()

app = Flask(__name__, template_folder="templates", static_folder="static")

client = CoordinatorClient(CFG["coordinator_url"])
assessor = CapabilityAssessor(CFG, client)
local_exec = LocalExecutor(CFG.get("pc_max_workers"))
PROJECTS = projects_mod.registry(CFG)
scheduler = Scheduler(CFG, client, assessor, local_exec, PROJECTS)
devacc = DevAccelerator(CFG, client, scheduler)

PROFILE = hardware.profile()


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ══════════════════════════════════════════════════════════════════════════
#  页面
# ══════════════════════════════════════════════════════════════════════════
@app.route("/")
def index():
    return render_template("dashboard.html")


# ══════════════════════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════════════════════
@app.route("/api/overview")
def api_overview():
    """控制台主数据：PC 硬件画像+利用率、协调端在线设备、当前运行状态。"""
    coord = client.status()
    phones = []
    if coord:
        for did, d in (coord.get("devices") or {}).items():
            if d.get("kind") == "phone" and d.get("online"):
                r = d.get("resources") or {}
                phones.append({"id": did, "name": d.get("name", did),
                               "level": d.get("level"), "cpu": r.get("cpu"),
                               "cpu_proc": r.get("cpu_proc"),
                               "mem": r.get("mem"), "cores": r.get("cores"),
                               "battery": r.get("battery"), "charging": r.get("charging")})
    return jsonify({
        "profile": PROFILE,
        "util": hardware.utilization(),
        "coordinator": {"url": CFG["coordinator_url"], "online": coord is not None,
                        "phones": phones},
        "run": scheduler.run.snapshot() if scheduler.run else None,
        "busy": scheduler.is_busy(),
        "lan_ip": lan_ip(),
        "version": config.VERSION,
        "dev_caps": devacc.caps(),
        "config": {k: CFG[k] for k in ("boss_port", "coordinator_url", "use_local_pc",
                                       "enable_gpu", "model_backend", "shard_size")},
    })


@app.route("/api/projects")
def api_projects():
    return jsonify({"projects": projects_mod.catalog(CFG)})


@app.route("/api/assess", methods=["POST"])
def api_assess():
    """立即重测各设备算力，返回分数 + 比例（不启动任务）。"""
    try:
        cap = assessor.assess(force=True)
        return jsonify({"ok": True, **cap})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/run", methods=["POST"])
def api_run():
    """启动一次加速运行。body: {project, params{}}"""
    j = request.get_json(force=True, silent=True) or {}
    ok, msg = scheduler.start(j.get("project", "prime_scan"), j.get("params", {}))
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/run_status")
def api_run_status():
    return jsonify(scheduler.run.snapshot() if scheduler.run else {"status": "idle"})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    scheduler.cancel()
    return jsonify({"ok": True})


# ── beta0.2 开发任务加速 ──
@app.route("/api/dev/run", methods=["POST"])
def api_dev_run():
    """启动开发任务。body: {task, params{}, resources{cpu,gpu,mem,disk,net}}"""
    j = request.get_json(force=True, silent=True) or {}
    ok, msg = devacc.start(j.get("task", "install_pip"), j.get("params", {}),
                           j.get("resources"))
    return jsonify({"ok": ok, "msg": msg})


@app.route("/api/dev/status")
def api_dev_status():
    return jsonify(devacc.snapshot())


@app.route("/api/dev/cancel", methods=["POST"])
def api_dev_cancel():
    devacc.cancel()
    return jsonify({"ok": True})


@app.route("/api/config", methods=["POST"])
def api_config():
    """更新部分配置（协调端地址/模型后端等），落盘。"""
    j = request.get_json(force=True, silent=True) or {}
    for k in ("coordinator_url", "model_backend", "model_path", "use_local_pc",
              "enable_gpu", "shard_size", "benchmark_seconds"):
        if k in j:
            CFG[k] = j[k]
    config.save(CFG)
    client.base = CFG["coordinator_url"].rstrip("/")
    return jsonify({"ok": True, "config": CFG})


if __name__ == "__main__":
    port = int(CFG["boss_port"])
    print("=" * 64)
    print(" 算力老板 Compute Boss")
    print(" 控制台:  http://127.0.0.1:%d" % port)
    print(" 局域网:  http://%s:%d" % (lan_ip(), port))
    print(" 协调端:  %s  (手机 worker 接这里)" % CFG["coordinator_url"])
    print(" PC 关键硬件: %d逻辑核 / %.1fGB内存 / GPU×%d" % (
        PROFILE.get("cpu", {}).get("logical", 0),
        PROFILE.get("mem", {}).get("total_mb", 0) / 1024,
        len(PROFILE.get("gpus", []))))
    print("=" * 64)
    app.run(host="0.0.0.0", port=port, threaded=True)
