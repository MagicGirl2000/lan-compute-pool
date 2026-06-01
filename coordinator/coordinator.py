# -*- coding: utf-8 -*-
"""
分布式算力协调端 (PC Coordinator)
=================================
系统大脑：跑在 PC 上，手机 worker(Android) 连进来贡献算力。

职责：
  1. 设备注册 / 心跳：手机/其它PC 上报自己的资源 + 安全阈值档位。
  2. 任务队列：把"你自己的项目任务"(转码/批处理/编译/计算)切片派给空闲 worker。
  3. 安全阈值守门：worker 处于 RED 不派活；本机 RED 也不接任务。
  4. 算力租用记账：CPU秒/内存MB·时/磁盘MB·时/流量MB/GPU秒，按单价算费用(或减免租金)。
  5. REST API + 看板：手机 worker 走 HTTP，PC 浏览器看 /dashboard。

★ 重要现实说明（写在代码里，免得误解）：
  - 本系统是"**你自己的** PC↔手机分布式算力"，用于加速**你自己的项目**。
  - 它**不能**把算力贡献给 Anthropic/Claude 换取 Pro 额度或优惠券——那种通道
    在现实中不存在，Claude 只运行在 Anthropic 自己的服务器上。本系统与之无关。
  - "算力租用"是你把自己的设备租给**你认识的第三方**用，自行结算（或抵你的租金）。

运行：
  .venv\Scripts\python.exe coordinator.py        # 默认 0.0.0.0:9000
  手机 worker 填 PC 的局域网 IP:9000 即可连入。
"""
import os
import json
import time
import random
import threading
import datetime
from flask import Flask, request, jsonify, Response

import safety

app = Flask(__name__)

VERSION = "beta-0.3"   # 三端同步版本号（协调端 / 安卓矿工 / Windows矿工 / 老板）
PORT = int(os.environ.get("CC_PORT", "9000"))
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(DATA_DIR, "cc_state.json")
BILLING_FILE = os.path.join(DATA_DIR, "cc_billing.json")

# ── 真实工分(beta0.3)：抽检复算 + 信誉。工分只发给"被验证为真"的活 ──
SPOT_CHECK_RATE = float(os.environ.get("CC_SPOTCHECK", "0.15"))  # 已完成任务被抽检复算的概率
STALE_JOB_SEC   = int(os.environ.get("CC_STALEJOB", "20"))       # running 超过此秒(且worker心跳旧)→重排
REP_START, REP_UP, REP_DOWN = 100.0, 1.0, 25.0                   # 信誉初始/复检通过+/复检失败-
# 计算"易变"字段：复算比对时忽略(每台机器这些值天然不同，不算造假)
_VOLATILE = {"ms", "kbps", "_acc", "lo", "hi", "rounds", "limit", "iterations", "samples", "seed", "url"}

# ── 算力租用单价（可改；单位：元）。减免租金 = 把别人欠你的费用抵掉你的成本 ──
PRICES = {
    "cpu_core_sec":   0.00002,   # 每核·秒
    "mem_mb_hour":    0.00001,   # 每 MB·小时
    "disk_mb_hour":   0.000002,  # 每 MB·小时
    "net_mb":         0.0001,    # 每 MB 流量
    "gpu_sec":        0.0005,    # 每 GPU·秒
}

# ── 运行时状态（内存 + 落盘）──────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "devices": {},   # device_id -> {name, kind, resources, level, last_seen, caps}
    "jobs": [],      # 待派 / 进行中 / 完成 的任务
    "job_seq": 0,
}
_thresholds = safety.load_thresholds()


def _load():
    global _state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                _state = json.load(f)
        except Exception:
            pass
    for k in ("devices", "jobs"):
        _state.setdefault(k, {} if k == "devices" else [])
    _state.setdefault("job_seq", 0)


def _save():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("[save error]", e)


def _now():
    return time.time()


def _ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 计费 ────────────────────────────────────────────────────────────────────
def add_billing(device_id, usage):
    """usage: dict(cpu_core_sec, mem_mb_hour, disk_mb_hour, net_mb, gpu_sec)。累加计费。"""
    rec = {}
    if os.path.exists(BILLING_FILE):
        try:
            rec = json.load(open(BILLING_FILE, "r", encoding="utf-8"))
        except Exception:
            rec = {}
    d = rec.setdefault(device_id, {"usage": {}, "cost": 0.0})
    cost = 0.0
    for k, v in usage.items():
        d["usage"][k] = d["usage"].get(k, 0.0) + v
        cost += v * PRICES.get(k, 0.0)
    d["cost"] = round(d.get("cost", 0.0) + cost, 6)
    rec["_total_cost"] = round(sum(x["cost"] for k, x in rec.items()
                                   if isinstance(x, dict) and "cost" in x), 6)
    try:
        json.dump(rec, open(BILLING_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass
    return cost


# ── 真实工分 / 信誉 ──────────────────────────────────────────────────────────
def _load_billing():
    if os.path.exists(BILLING_FILE):
        try:
            return json.load(open(BILLING_FILE, "r", encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_billing(rec):
    try:
        json.dump(rec, open(BILLING_FILE, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    except Exception:
        pass


def award_credits(device_id, points, passed_check=None):
    """给设备记/扣工分 + 更新信誉。
    points: 本次工分增量(可负=撤销)。passed_check: None=非复检; True/False=复检结果。"""
    rec = _load_billing()
    d = rec.setdefault(device_id, {"usage": {}, "cost": 0.0})
    d.setdefault("credits", 0.0)
    d.setdefault("reputation", REP_START)
    d.setdefault("checks_passed", 0)
    d.setdefault("checks_failed", 0)
    d["credits"] = round(max(0.0, d["credits"] + points), 3)
    if passed_check is True:
        d["checks_passed"] += 1
        d["reputation"] = min(100.0, d["reputation"] + REP_UP)
    elif passed_check is False:
        d["checks_failed"] += 1
        d["reputation"] = max(0.0, d["reputation"] - REP_DOWN)
    _save_billing(rec)
    return d["credits"], d["reputation"]


def _canon(result):
    """复算比对用的规范化结果：丢掉每台机器天然不同的易变字段。"""
    if not isinstance(result, dict):
        return result
    return {k: v for k, v in result.items() if k not in _VOLATILE}


def _job_points(usage):
    """这份活值多少工分 = 真实 CPU 核秒(干得多得得多)。"""
    return round(float((usage or {}).get("cpu_core_sec", 0.0)), 3)


# ══════════════════════════════════════════════════════════════════════════
#  REST API（手机 worker 调用）
# ══════════════════════════════════════════════════════════════════════════

@app.route("/api/register", methods=["POST"])
def api_register():
    """worker 注册/心跳。body: {device_id, name, kind, resources{}, caps{}, net_session_mb}"""
    j = request.get_json(force=True, silent=True) or {}
    did = j.get("device_id") or request.remote_addr
    res = j.get("resources", {})
    net_mb = float(j.get("net_session_mb", 0))
    level, reasons = safety.evaluate(res, _thresholds, net_mb)
    with _lock:
        prev = _state["devices"].get(did, {})
        _state["devices"][did] = {
            "name": j.get("name", did),
            "kind": j.get("kind", "phone"),
            "resources": res,
            "caps": j.get("caps", {}),
            "level": level,
            "reasons": reasons,
            "net_session_mb": net_mb,
            "ver": j.get("ver"),
            "power_level": j.get("power_level"),        # 节点自报的能效档
            "forced_level": prev.get("forced_level"),   # 中央越权下发的档(保留)
            "last_seen": _now(),
            "last_seen_str": _ts(),
        }
        forced = _state["devices"][did].get("forced_level")
        _save()
    # 中央越权：若管理员给这台/全部设了 forced_level，随心跳下发，节点照此执行
    return jsonify({"ok": True, "level": level, "reasons": reasons,
                    "thresholds": _thresholds, "forced_level": forced})


@app.route("/api/pull_job", methods=["POST"])
def api_pull_job():
    """worker 拉任务。先过安全阈值；RED 不给活。返回一个待办任务或 null。
    ★ 按档位 + 每个任务自身 weight 过滤：
      RED    → 全拒
      YELLOW → 只派 weight=small 的任务
      GREEN  → 派任意任务
    （之前 bug：写死按 "normal" 判定，导致 YELLOW 档时连标了 small 的任务也不派）"""
    j = request.get_json(force=True, silent=True) or {}
    did = j.get("device_id") or request.remote_addr
    res = j.get("resources", {})
    net_mb = float(j.get("net_session_mb", 0))
    level, reasons = safety.evaluate(res, _thresholds, net_mb)
    if level == "RED":
        return jsonify({"ok": True, "job": None, "level": level,
                        "reasons": reasons, "paused": True})
    # worker 能力（caps）：用于按能力路由任务。download 类谁都能接；
    # build/python 类只发给具备该能力的设备（手机没 SDK 就不会派到 build APK）。
    worker_caps = set(k for k, v in (j.get("caps") or {}).items() if v)
    with _lock:
        if did in _state["devices"]:        # 任何接触都刷新心跳→不被误判离线/被reaper误收
            _state["devices"][did]["last_seen"] = _now()
        job = None
        for jb in _state["jobs"]:
            if jb["status"] != "queued":
                continue
            # YELLOW 档只接 small 任务
            if level == "YELLOW" and jb.get("weight", "normal") != "small":
                continue
            # 能力匹配：任务声明的 requires 必须被 worker 的 caps 全覆盖
            reqs = set(jb.get("requires") or [])
            if reqs and not reqs.issubset(worker_caps):
                continue
            # 复检任务不能再发回原 worker（必须换一台设备复算才有意义）
            if jb.get("exclude_worker") == did:
                continue
            jb["status"] = "running"
            jb["worker"] = did
            jb["started"] = _ts()
            jb["started_ts"] = _now()   # 给"卡死任务重排"reaper 用
            job = jb
            break
        _save()
    paused = (job is None and level == "YELLOW")
    forced = _state["devices"].get(did, {}).get("forced_level")
    return jsonify({"ok": True, "job": job, "level": level,
                    "reasons": reasons, "paused": paused, "forced_level": forced})


@app.route("/api/complete_job", methods=["POST"])
def api_complete_job():
    """worker 交结果 + 上报用量 → 记账 + 真实工分(抽检复算)。
    ★真实工分逻辑：
      - 普通任务完成：以 SPOT_CHECK_RATE 概率【抽检】→ 派复算副本给【另一台】设备，
        暂不发工分；其余直接发工分。
      - 复算任务完成：与原任务结果比对(去易变字段)。一致→原worker得工分+信誉↑；
        不一致→原worker工分撤销+信誉↓，并用【可信的复算结果纠正原任务】(保证总结果对)。"""
    j = request.get_json(force=True, silent=True) or {}
    jid = j.get("job_id")
    usage = j.get("usage", {})
    did = j.get("device_id") or request.remote_addr
    result = j.get("result")
    cost = add_billing(did, usage)
    points = _job_points(usage)

    with _lock:
        if did in _state["devices"]:        # 交活也刷新心跳
            _state["devices"][did]["last_seen"] = _now()
        job = next((x for x in _state["jobs"] if x["id"] == jid), None)
        if job is None:
            _save()
            return jsonify({"ok": True, "billed": cost})
        job["status"] = "done"
        job["result"] = result
        job["finished"] = _ts()
        job["cost"] = cost
        job["worker"] = did
        verify_of = job.get("verify_of")
        _save()

    # ── 这是一个"复算任务" ──
    if verify_of:
        with _lock:
            orig = next((x for x in _state["jobs"] if x["id"] == verify_of), None)
        if orig is not None:
            ov = orig.get("worker_orig") or orig.get("worker")
            ok = (_canon(result) == _canon(orig.get("result")))
            if ok:
                award_credits(ov, orig.get("points", 0), passed_check=True)   # 原活是真的→发工分
                award_credits(did, round(points * 0.5, 3))                    # 复检者也得工分
                with _lock:
                    orig["verify"] = "passed"; orig["credited"] = True
            else:
                award_credits(ov, 0, passed_check=False)                      # 撤销+扣信誉
                award_credits(did, round(points * 0.5, 3), passed_check=True)
                with _lock:
                    orig["result"] = result        # 用可信结果纠正原任务(保证整体计算正确)
                    orig["verify"] = "FAILED"; orig["credited"] = False
            with _lock:
                _save()
            return jsonify({"ok": True, "billed": cost, "verify_of": verify_of, "passed": ok})
        return jsonify({"ok": True, "billed": cost})

    # ── 普通任务：抽检 or 直接发工分 ──
    with _lock:
        job["points"] = points
        job["worker_orig"] = did
    if random.random() < SPOT_CHECK_RATE:
        with _lock:
            _state["job_seq"] += 1
            vjid = "J%05d" % _state["job_seq"]
            _state["jobs"].append({
                "id": vjid, "type": job["type"], "payload": job.get("payload", {}),
                "weight": job.get("weight", "normal"), "requires": job.get("requires", []),
                "exclude_worker": did, "verify_of": jid,
                "status": "queued", "created": _ts(), "worker": None, "result": None})
            job["verify"] = "pending"; job["credited"] = False
            _save()
        return jsonify({"ok": True, "billed": cost, "spotcheck": vjid})
    else:
        credits, rep = award_credits(did, points)
        with _lock:
            job["credited"] = True; job["verify"] = "trusted"
        return jsonify({"ok": True, "billed": cost, "credits": credits, "reputation": rep})


@app.route("/api/submit_job", methods=["POST"])
def api_submit_job():
    """你自己提交一个要加速的任务。body: {type, payload, weight}"""
    j = request.get_json(force=True, silent=True) or {}
    with _lock:
        _state["job_seq"] += 1
        jid = "J%05d" % _state["job_seq"]
        job = {
            "id": jid,
            "type": j.get("type", "echo"),
            "payload": j.get("payload", {}),
            "weight": j.get("weight", "normal"),
            "requires": j.get("requires", []),   # 能力要求,如 ["download"] / ["build"]
            "status": "queued",
            "created": _ts(),
            "worker": None,
            "result": None,
        }
        _state["jobs"].append(job)
        _save()
    return jsonify({"ok": True, "job_id": jid})


@app.route("/api/status")
def api_status():
    # 清理超时设备（60s 无心跳→离线；放宽以容忍 AVD↔宿主机链路偶尔丢包）
    with _lock:
        now = _now()
        for did, dv in _state["devices"].items():
            dv["online"] = (now - dv.get("last_seen", 0)) < 60
        devices = dict(_state["devices"])
        jobs = list(_state["jobs"])[-50:]
    billing = {}
    if os.path.exists(BILLING_FILE):
        try:
            billing = json.load(open(BILLING_FILE, "r", encoding="utf-8"))
        except Exception:
            pass
    # 本机资源
    local = safety.read_resources()
    local_level, local_reasons = safety.evaluate(local, _thresholds)
    return jsonify({"devices": devices, "jobs": jobs, "billing": billing,
                    "local": local, "local_level": local_level,
                    "local_reasons": local_reasons, "thresholds": _thresholds,
                    "prices": PRICES, "version": VERSION})


@app.route("/api/version")
def api_version():
    return jsonify({"version": VERSION})


@app.route("/api/set_level", methods=["POST"])
def api_set_level():
    """中央越权设能效档。body: {device_id: "<id>"|"*", level: 1-5}
    "*" = 全局所有设备统一设档(集中力量办大事/大跃进式动员)。
    ★注意：能效档只调'挣钱速度/投入强度'，安全阈值仍是每个节点不可剥夺的自保底线
    (过热/低电照样自动暂停)——这是大跃进缺失的'基层否决权'，本系统保留。"""
    j = request.get_json(force=True, silent=True) or {}
    did = j.get("device_id", "*")
    lvl = min(5, max(1, int(j.get("level", 3))))
    with _lock:
        targets = list(_state["devices"].keys()) if did == "*" else [did]
        n = 0
        for d in targets:
            if d in _state["devices"]:
                _state["devices"][d]["forced_level"] = lvl
                n += 1
        _save()
    return jsonify({"ok": True, "level": lvl, "affected": n})


@app.route("/api/jobs")
def api_jobs():
    """按 id 批量查任务（不受 /api/status 只回最近50条的限制）。?ids=J1,J2"""
    want = set(i for i in (request.args.get("ids", "").split(",")) if i)
    with _lock:
        out = {j["id"]: j for j in _state["jobs"] if j["id"] in want}
    return jsonify({"jobs": out})


# ══════════════════════════════════════════════════════════════════════════
#  看板（PC 浏览器）
# ══════════════════════════════════════════════════════════════════════════

@app.route("/")
@app.route("/dashboard")
def dashboard():
    return Response(DASHBOARD_HTML, mimetype="text/html")


DASHBOARD_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>分布式算力协调端</title>
<style>
body{font-family:sans-serif;background:#0f1420;color:#e0e6f0;margin:0;padding:16px}
h1{font-size:20px;color:#5ad}
.card{background:#1a2030;border-radius:10px;padding:14px;margin:10px 0;box-shadow:0 2px 8px rgba(0,0,0,.3)}
.lv-GREEN{color:#4d8}.lv-YELLOW{color:#fd5}.lv-RED{color:#f66;font-weight:bold}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #2a3040}
th{color:#89a}
.bar{height:8px;background:#2a3040;border-radius:4px;overflow:hidden;display:inline-block;width:80px;vertical-align:middle}
.bar>i{display:block;height:100%}
.g{background:#4d8}.y{background:#fd5}.r{background:#f66}
.pill{padding:2px 8px;border-radius:10px;font-size:11px;background:#2a3040}
small{color:#789}
</style></head><body>
<h1>⚡ 分布式算力协调端 <small>(你自己的 PC↔手机算力，用于加速你的项目)</small></h1>
<div class="card"><b>本机 PC</b> <span id="localLevel"></span><div id="local"></div></div>
<div class="card"><b>接入设备（手机/其它PC）</b><div id="devices"></div></div>
<div class="card"><b>任务队列</b><div id="jobs"></div></div>
<div class="card"><b>算力租用记账</b> <small>(单价见下；可用于抵你的租金)</small><div id="billing"></div></div>
<script>
function bar(pct,cls){pct=Math.min(100,pct||0);return '<span class="bar"><i class="'+cls+'" style="width:'+pct+'%"></i></span> '+pct.toFixed(0)+'%';}
function cls(pct,y,r){return pct>=r?'r':pct>=y?'y':'g';}
async function tick(){
 let s=await (await fetch('/api/status')).json();
 let L=s.local, th=s.thresholds;
 document.getElementById('localLevel').innerHTML='<span class="lv-'+s.local_level+'">●'+s.local_level+'</span> <small>'+s.local_reasons.join('; ')+'</small>';
 document.getElementById('local').innerHTML=
   'CPU '+bar(L.cpu,cls(L.cpu,th.cpu_yellow,th.cpu_red))+
   ' &nbsp; 内存 '+bar(L.mem,cls(L.mem,th.mem_yellow,th.mem_red))+
   ' &nbsp; 磁盘 '+bar(L.disk,cls(L.disk,th.disk_yellow,th.disk_red))+
   ' &nbsp; <small>'+L.cores+'核 / '+(L.mem_total_mb/1024).toFixed(1)+'GB'+
   (L.battery!=null?(' / 电池'+L.battery+'%'+(L.charging?'⚡':'')):'')+'</small>';
 let dh='<table><tr><th>设备</th><th>类型</th><th>档位</th><th>CPU</th><th>内存</th><th>电池</th><th>在线</th></tr>';
 for(let id in s.devices){let d=s.devices[id],r=d.resources||{};
   dh+='<tr><td>'+d.name+'</td><td>'+d.kind+'</td><td class="lv-'+d.level+'">●'+d.level+'</td>'+
   '<td>'+bar(r.cpu,cls(r.cpu,th.cpu_yellow,th.cpu_red))+'</td>'+
   '<td>'+bar(r.mem,cls(r.mem,th.mem_yellow,th.mem_red))+'</td>'+
   '<td>'+(r.battery!=null?r.battery+'%'+(r.charging?'⚡':''):'-')+'</td>'+
   '<td>'+(d.online?'<span class="pill" style="color:#4d8">在线</span>':'<span class="pill">离线</span>')+'</td></tr>';}
 dh+='</table>'; if(Object.keys(s.devices).length==0)dh='<small>暂无设备接入。手机装 worker app，填本机IP:'+location.port+'</small>';
 document.getElementById('devices').innerHTML=dh;
 let jh='<table><tr><th>ID</th><th>类型</th><th>状态</th><th>worker</th><th>费用</th></tr>';
 s.jobs.slice(-15).reverse().forEach(j=>{jh+='<tr><td>'+j.id+'</td><td>'+j.type+'</td><td>'+j.status+'</td><td>'+(j.worker||'-')+'</td><td>'+(j.cost?j.cost.toFixed(4):'-')+'</td></tr>';});
 jh+='</table>'; if(s.jobs.length==0)jh='<small>暂无任务</small>';
 document.getElementById('jobs').innerHTML=jh;
 let bh='<table><tr><th>设备</th><th>CPU核秒</th><th>内存MB时</th><th>流量MB</th><th>GPU秒</th><th>费用(元)</th></tr>';
 for(let id in s.billing){if(id[0]=='_')continue;let b=s.billing[id],u=b.usage||{};
   bh+='<tr><td>'+id+'</td><td>'+(u.cpu_core_sec||0).toFixed(1)+'</td><td>'+(u.mem_mb_hour||0).toFixed(1)+'</td><td>'+(u.net_mb||0).toFixed(1)+'</td><td>'+(u.gpu_sec||0).toFixed(1)+'</td><td>'+(b.cost||0).toFixed(4)+'</td></tr>';}
 bh+='</table>'+'<div style="margin-top:6px"><b>总计：'+((s.billing._total_cost)||0).toFixed(4)+' 元</b></div>';
 document.getElementById('billing').innerHTML=bh;
}
tick(); setInterval(tick,2000);
</script></body></html>"""


def _reaper():
    """卡死任务重排：worker 领了活却心跳停了(锁屏被冻/掉线)→把它的 running 任务退回队列，
    让活着的设备重新接走。这就是修"半死节点拖累集体/一顿一顿"的关键。"""
    while True:
        time.sleep(5)
        now = _now()
        moved = 0
        with _lock:
            for jb in _state["jobs"]:
                if jb.get("status") != "running":
                    continue
                w = jb.get("worker")
                last = _state["devices"].get(w, {}).get("last_seen", 0)
                if now - last > STALE_JOB_SEC:        # worker 心跳已旧 → 它多半冻住了
                    jb["status"] = "queued"
                    jb["worker"] = None
                    jb.pop("started_ts", None)
                    moved += 1
            if moved:
                _save()
        if moved:
            print("[reaper] 重排 %d 个卡死任务(worker心跳超时)" % moved)


if __name__ == "__main__":
    _load()
    threading.Thread(target=_reaper, daemon=True).start()
    print("=" * 60)
    print(" 分布式算力协调端 (%s) http://0.0.0.0:%d" % (VERSION, PORT))
    print(" 看板: http://127.0.0.1:%d/dashboard" % PORT)
    print(" 手机 worker 连: http://<本机局域网IP>:%d" % PORT)
    print(" 安全阈值:", _thresholds["cpu_red"], "CPU%/",
          _thresholds["mem_red"], "MEM%/", _thresholds["temp_red"], "℃")
    print("=" * 60)
    app.run(host="0.0.0.0", port=PORT, threaded=True)
