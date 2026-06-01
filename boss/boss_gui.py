# -*- coding: utf-8 -*-
"""
算力老板 · Windows 桌面 GUI (Tkinter)
====================================
和 boss.py(Web 控制台)同一套后端，但用原生 Windows 窗口呈现，不依赖浏览器。

  - PC 关键硬件实时仪表（CPU/每核/内存/磁盘/GPU/网络）
  - 算力能力比例条（PC vs 各手机，按此切分任务）
  - 在线手机 worker 列表
  - 选项目 → 按比例切分 → 派发(PC 多核 + 手机经协调端) → 实时任务流 + 结果

设计要点：
  · 网络(协调端)与硬件读取放【后台线程】，Tk 主循环只读快照重绘，界面不卡。
  · 所有 Tk 创建都在 main() 内，模块导入时零副作用——因为 multiprocessing(spawn)
    会在子进程里 re-import 本文件，模块级若建窗口会出错。

运行：.venv\\Scripts\\python.exe boss_gui.py   或双击  启动老板GUI.bat
"""
import json
import socket
import threading
import time
import tkinter as tk
from tkinter import ttk

import config
import hardware
from coordinator_client import CoordinatorClient
from capability import CapabilityAssessor
from executor import LocalExecutor
import projects as projects_mod
from scheduler import Scheduler
from devtasks import DevAccelerator
from globalmode import GlobalMode

# ── 配色（深色主题）──
BG = "#0d1118"; PANEL = "#161c28"; PANEL2 = "#1d2533"; LINE = "#2a3344"
TXT = "#dfe6f2"; MUT = "#8295ad"; ACC = "#4ea1ff"; ACC2 = "#7c5cff"
G = "#42d392"; Y = "#ffcc52"; R = "#ff6b6b"
SEG_COLORS = ["#4ea1ff", "#42d392", "#ffcc52", "#7c5cff", "#ff8a5c", "#5cc8ff"]


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def lvl_color(pct, y=75, r=90):
    return R if pct >= r else (Y if pct >= y else G)


class BossGUI:
    def __init__(self, root):
        self.root = root
        # ── 后端 ──
        self.cfg = config.load()
        self.client = CoordinatorClient(self.cfg["coordinator_url"], timeout=4)
        self.assessor = CapabilityAssessor(self.cfg, self.client)
        self.executor = LocalExecutor(self.cfg.get("pc_max_workers"))
        self.projects = projects_mod.registry(self.cfg)
        self.catalog = projects_mod.catalog(self.cfg)
        self.scheduler = Scheduler(self.cfg, self.client, self.assessor,
                                   self.executor, self.projects)
        self.devacc = DevAccelerator(self.cfg, self.client, self.scheduler)
        self.profile = hardware.profile()
        self.glob = GlobalMode(self.cfg, self.client, self.profile)
        # ── 后台轮询共享状态 ──
        self.state = {"util": hardware.utilization(), "coord": None}
        self.last_ratio = {}     # 上次评估的比例（设备 -> dict）
        self.assessing = False
        self._poll_stop = False

        self._build()
        threading.Thread(target=self._poller, daemon=True).start()
        self._refresh()

    # ════════════════════════════════════════════════════════════════
    #  界面搭建
    # ════════════════════════════════════════════════════════════════
    def _build(self):
        self.root.title("算力老板 · Compute Boss")
        self.root.configure(bg=BG)
        self.root.geometry("1000x900")
        self.root.minsize(880, 600)

        # 顶栏
        top = tk.Frame(self.root, bg="#141b2a")
        top.pack(fill="x")
        tk.Label(top, text="⚡ 算力老板", bg="#141b2a", fg=TXT,
                 font=("Segoe UI", 16, "bold")).pack(side="left", padx=14, pady=10)
        tk.Label(top, text="局域网算力编排", bg="#141b2a", fg=MUT,
                 font=("Segoe UI", 9)).pack(side="left")
        tk.Label(top, text=" %s " % config.VERSION, bg=ACC2, fg="#06101f",
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=6)
        self.lb_lan = tk.Label(top, text="", bg="#141b2a", fg=MUT, font=("Segoe UI", 9))
        self.lb_lan.pack(side="right", padx=10)
        self.lb_coord = tk.Label(top, text="协调端…", bg="#141b2a", fg=MUT,
                                 font=("Segoe UI", 9))
        self.lb_coord.pack(side="right")

        # —— 可滚动主体（卡片很多，必须能滚，否则下半部分被截断）——
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # 鼠标滚轮（Windows）
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        inner = tk.Frame(body, bg=BG)
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        # —— 上排：PC 硬件 | 手机 ——
        rowh = tk.Frame(inner, bg=BG)
        rowh.pack(fill="x")
        self._card_pc(rowh)
        self._card_phones(rowh)

        # —— 全局模式 / 一体机 ——
        self._card_global(inner)
        # —— 共享设置 (beta0.3) ——
        self._card_sharing(inner)
        # —— 算力比例 ——
        self._card_ratio(inner)
        # —— 项目运行 ——
        self._card_runner(inner)
        # —— 进度 / 任务流 ——
        self._card_progress(inner)
        # —— 开发任务加速 (beta0.2) ——
        self._card_dev(inner)
        # —— 结果 / 日志 ——
        self._card_output(inner)

    def _card(self, parent, title, sub="", side=None, width=None):
        outer = tk.Frame(parent, bg=PANEL, highlightbackground=LINE,
                         highlightthickness=1)
        if side:
            outer.pack(side=side, fill="both", expand=True, padx=(0, 8) if side == "left" else 0, pady=4)
        else:
            outer.pack(fill="x", pady=5)
        head = tk.Frame(outer, bg=PANEL)
        head.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(head, text=title, bg=PANEL, fg=TXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        if sub:
            tk.Label(head, text=sub, bg=PANEL, fg=MUT,
                     font=("Segoe UI", 8)).pack(side="left", padx=8)
        inner = tk.Frame(outer, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        return outer, head, inner

    def _card_pc(self, parent):
        cpu = self.profile.get("cpu", {})
        mem = self.profile.get("mem", {})
        disk = self.profile.get("disk", {})
        gpus = self.profile.get("gpus", [])
        spec = "%d物理/%d逻辑核 · %dMHz · %.1fGB · 盘%.0fGB" % (
            cpu.get("physical", 0), cpu.get("logical", 0), cpu.get("freq_mhz", 0),
            mem.get("total_mb", 0) / 1024, disk.get("total_gb", 0))
        if gpus:
            spec += " · GPU:" + ",".join(g["name"] for g in gpus)
        else:
            spec += " · 无独显"
        _, _, inner = self._card(parent, "💻 本机 PC · 关键硬件", side="left")
        tk.Label(inner, text=spec, bg=PANEL, fg=MUT, font=("Segoe UI", 8),
                 wraplength=430, justify="left").pack(anchor="w", pady=(0, 6))
        # 硬件仪表 canvas（每次重绘）
        self.cv_hw = tk.Canvas(inner, bg=PANEL, height=150, highlightthickness=0)
        self.cv_hw.pack(fill="x")

    def _card_phones(self, parent):
        _, _, inner = self._card(parent, "📱 手机 worker", "接在协调端上的工人", side="right")
        self.fr_phones = inner
        self.lb_phone_empty = tk.Label(inner, text="暂无手机接入", bg=PANEL, fg=MUT,
                                       font=("Segoe UI", 9), justify="left", wraplength=420)
        self.lb_phone_empty.pack(anchor="w")
        self._phone_widgets = []

    def _card_sharing(self, parent):
        _, _, inner = self._card(parent, "🔌 共享设置",
                                 "可选是否把本机关键设备共享进算力池(关掉=不贡献也不占用)")
        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x")
        self.share_vars = {}
        for key, label in [("share_cpu", "CPU"), ("share_gpu", "GPU"), ("share_mem", "内存"),
                           ("share_disk", "硬盘"), ("share_net", "网络")]:
            v = tk.IntVar(value=1 if self.cfg.get(key, True) else 0)
            self.share_vars[key] = v
            tk.Checkbutton(row, text=label, variable=v, bg=PANEL, fg=TXT, selectcolor=PANEL2,
                           activebackground=PANEL, activeforeground=TXT, font=("Segoe UI", 9),
                           command=lambda k=key: self._on_share(k)).pack(side="left", padx=6)
        self.share_saved = tk.Label(inner, text="", bg=PANEL, fg=G, font=("Segoe UI", 8))
        self.share_saved.pack(anchor="w", pady=(4, 0))

    def _on_share(self, key):
        on = bool(self.share_vars[key].get())
        self.cfg[key] = on
        if key == "share_cpu":
            self.cfg["use_local_pc"] = on   # 不共享CPU → 本机不参与算力分担
        try:
            config.save(self.cfg)
        except Exception:
            pass
        self.share_saved.config(text="已保存 ✓")
        self.root.after(1500, lambda: self.share_saved.config(text=""))

    def _card_global(self, parent):
        _, head, inner = self._card(parent, "🧊 全局模式 · 一体机",
                                    "PC+手机+模拟器 抽象成一台机：空闲端帮忙，忙的端把重活交出去")
        self.btn_global = tk.Button(head, text="全局模式：关", command=self.on_global_toggle,
                                    bg=PANEL2, fg=TXT, relief="flat", font=("Segoe UI", 9),
                                    cursor="hand2", activebackground=G)
        self.btn_global.pack(side="right")
        # 一体机合计指标
        mrow = tk.Frame(inner, bg=PANEL)
        mrow.pack(fill="x")
        self.om_vars = {}
        for key, label in [("cores", "逻辑核"), ("mem", "GB内存"), ("gpu", "GPU"),
                           ("nodes", "节点"), ("idle", "空闲核·可助")]:
            cell = tk.Frame(mrow, bg=PANEL2, highlightbackground=LINE, highlightthickness=1)
            cell.pack(side="left", fill="both", expand=True, padx=3)
            v = tk.Label(cell, text="0", bg=PANEL2, fg=ACC, font=("Segoe UI", 20, "bold"))
            v.pack(pady=(8, 0))
            tk.Label(cell, text=label, bg=PANEL2, fg=MUT, font=("Segoe UI", 8)).pack(pady=(0, 8))
            self.om_vars[key] = v
        # 节点角色列表
        self.om_nodes = tk.Frame(inner, bg=PANEL)
        self.om_nodes.pack(fill="x", pady=(8, 0))
        self._om_node_widgets = []
        tk.Label(inner, text="用法：任意程序  from pool_client import Pool;  "
                             "Pool(url).map(\"compute\", items)  即可把重计算撒给整池（见 examples/）",
                 bg=PANEL, fg=MUT, font=("Segoe UI", 8), wraplength=900,
                 justify="left").pack(anchor="w", pady=(6, 0))

    def on_global_toggle(self):
        on = self.glob.toggle()
        self.cfg["global_mode"] = on
        try:
            config.save(self.cfg)
        except Exception:
            pass

    def _refresh_global(self):
        g = self.glob.status()
        self.btn_global.config(
            text="全局模式：" + ("开" if g["on"] else "关"),
            bg=(G if g["on"] else PANEL2), fg=("#06101f" if g["on"] else TXT))
        self.om_vars["cores"].config(text=str(g["total_cores"]))
        self.om_vars["mem"].config(text=str(g["total_mem_gb"]))
        self.om_vars["gpu"].config(text=str(g["total_gpu"]))
        self.om_vars["nodes"].config(text=str(g["node_count"]))
        self.om_vars["idle"].config(text=str(g["idle_cores"]))
        for w in self._om_node_widgets:
            w.destroy()
        self._om_node_widgets = []
        for n in g["nodes"]:
            fr = tk.Frame(self.om_nodes, bg=PANEL2)
            fr.pack(side="left", padx=3, pady=2)
            ic = "💻" if n["kind"] == "pc" else "📱"
            tk.Label(fr, text="%s %s" % (ic, n["name"]), bg=PANEL2, fg=TXT,
                     font=("Segoe UI", 9)).pack(side="left", padx=(8, 4), pady=4)
            tk.Label(fr, text="%d核 %.1fGB" % (n["cores"], n["mem_gb"]), bg=PANEL2, fg=MUT,
                     font=("Segoe UI", 8)).pack(side="left", padx=4)
            busy = n["busy"]
            tk.Label(fr, text=n["role"], bg=PANEL2, fg=(Y if busy else G),
                     font=("Segoe UI", 8)).pack(side="left", padx=(4, 8))
            self._om_node_widgets.append(fr)

    def _card_ratio(self, parent):
        _, head, inner = self._card(parent, "🧮 算力能力比例", "老板按此把任务分给各设备（强的多分）")
        self.btn_assess = tk.Button(head, text="重新评估算力", command=self.on_assess,
                                    bg=PANEL2, fg=TXT, relief="flat", font=("Segoe UI", 9),
                                    activebackground=ACC, cursor="hand2")
        self.btn_assess.pack(side="right")
        self.cv_ratio = tk.Canvas(inner, bg=PANEL, height=44, highlightthickness=0)
        self.cv_ratio.pack(fill="x")
        self.lb_legend = tk.Label(inner, text="尚未评估", bg=PANEL, fg=MUT,
                                  font=("Segoe UI", 9), justify="left")
        self.lb_legend.pack(anchor="w", pady=(8, 0))

    def _card_runner(self, parent):
        _, _, inner = self._card(parent, "🚀 加速项目",
                                 "选项目 → 按算力比例切分 → 分发到 PC + 手机")
        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x")
        self.proj_var = tk.StringVar()
        names = ["%s" % p["title"] for p in self.catalog]
        self.proj_keys = [p["key"] for p in self.catalog]
        self.cmb = ttk.Combobox(row, values=names, state="readonly", width=24,
                                textvariable=self.proj_var)
        self.cmb.current(0)
        self.cmb.pack(side="left")
        self.cmb.bind("<<ComboboxSelected>>", lambda e: self._fill_params())
        self.ent_params = tk.Entry(row, bg=PANEL2, fg=TXT, insertbackground=TXT,
                                   relief="flat", font=("Consolas", 10))
        self.ent_params.pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        self.btn_run = tk.Button(row, text="开始加速", command=self.on_run, bg=ACC,
                                 fg="#06101f", relief="flat", font=("Segoe UI", 9, "bold"),
                                 cursor="hand2", activebackground=ACC2)
        self.btn_run.pack(side="left")
        self.btn_cancel = tk.Button(row, text="取消", command=self.on_cancel, bg="#3a1d22",
                                    fg="#ff9b9b", relief="flat", font=("Segoe UI", 9),
                                    cursor="hand2", state="disabled")
        self.btn_cancel.pack(side="left", padx=(6, 0))
        self.lb_desc = tk.Label(inner, text="", bg=PANEL, fg=MUT, font=("Segoe UI", 8))
        self.lb_desc.pack(anchor="w", pady=(6, 0))
        self._fill_params()

    def _card_progress(self, parent):
        _, _, inner = self._card(parent, "📊 运行进度 / 任务流")
        prow = tk.Frame(inner, bg=PANEL)
        prow.pack(fill="x")
        self.cv_prog = tk.Canvas(prow, bg=PANEL2, height=22, highlightthickness=0)
        self.cv_prog.pack(side="left", fill="x", expand=True)
        self.lb_runstat = tk.Label(inner, text="空闲", bg=PANEL, fg=MUT,
                                   font=("Segoe UI", 9))
        self.lb_runstat.pack(anchor="w", pady=(6, 4))
        self.cv_flow = tk.Canvas(inner, bg=PANEL, height=10, highlightthickness=0)
        self.cv_flow.pack(fill="x")

    DEV_DEFAULTS = {
        "parallel_compute": '{"project":"prime_scan","params":{"limit":2000000,"chunk":50000}}',
        "install_pip": '{"packages":"numpy pandas requests"}',
        "install_plugin": '{"urls":"https://.../a.zip https://.../b.jar"}',
        "build_apk": '{"project_dir":"D:\\\\proj\\\\MyApp","task":"assembleDebug"}',
        "deploy_apk": '{"apk":"","project_dir":"D:\\\\proj\\\\MyApp"}',
        "update_workers": '{"worker_apk":"D:\\\\...\\\\app-release.apk"}',
    }
    DEV_LABELS = [
        ("parallel_compute", "并行计算"), ("install_pip", "装库(pip)"),
        ("install_plugin", "装插件/下载"), ("build_apk", "构建APK"),
        ("deploy_apk", "部署真机"), ("update_workers", "更新工人app"),
    ]

    def _card_dev(self, parent):
        _, head, inner = self._card(parent, "🛠 开发任务加速",
                                    "并行计算 · 装库/装插件 · 构建APK · 一键部署真机")
        self.dev_caps_lb = tk.Label(head, text="", bg=PANEL, fg=MUT, font=("Segoe UI", 8))
        self.dev_caps_lb.pack(side="right")
        # 资源勾选
        resrow = tk.Frame(inner, bg=PANEL)
        resrow.pack(fill="x")
        tk.Label(resrow, text="资源共享:", bg=PANEL, fg=MUT, font=("Segoe UI", 9)).pack(side="left")
        self.res_vars = {}
        for key, label, dflt in [("cpu", "CPU", 1), ("gpu", "GPU(含手机)", 0),
                                 ("mem", "内存", 1), ("disk", "硬盘", 1), ("net", "网络", 1)]:
            v = tk.IntVar(value=dflt)
            self.res_vars[key] = v
            tk.Checkbutton(resrow, text=label, variable=v, bg=PANEL, fg=TXT,
                           selectcolor=PANEL2, activebackground=PANEL, activeforeground=TXT,
                           font=("Segoe UI", 9)).pack(side="left", padx=4)
        # 任务选择 + 参数 + 按钮
        row = tk.Frame(inner, bg=PANEL)
        row.pack(fill="x", pady=(6, 0))
        self.dev_task_var = tk.StringVar(value="parallel_compute")
        names = [n for _, n in self.DEV_LABELS]
        self.dev_keys = [k for k, _ in self.DEV_LABELS]
        self.dev_cmb = ttk.Combobox(row, values=names, state="readonly", width=14)
        self.dev_cmb.current(0)
        self.dev_cmb.pack(side="left")
        self.dev_cmb.bind("<<ComboboxSelected>>", lambda e: self._dev_fill())
        self.dev_params = tk.Entry(row, bg=PANEL2, fg=TXT, insertbackground=TXT,
                                   relief="flat", font=("Consolas", 9))
        self.dev_params.pack(side="left", fill="x", expand=True, padx=8, ipady=3)
        self.btn_dev = tk.Button(row, text="运行", command=self.on_dev_run, bg=ACC,
                                 fg="#06101f", relief="flat", font=("Segoe UI", 9, "bold"),
                                 cursor="hand2")
        self.btn_dev.pack(side="left")
        self.btn_dev_cancel = tk.Button(row, text="取消", command=self.on_dev_cancel,
                                        bg="#3a1d22", fg="#ff9b9b", relief="flat",
                                        font=("Segoe UI", 9), cursor="hand2", state="disabled")
        self.btn_dev_cancel.pack(side="left", padx=(6, 0))
        self.dev_stat = tk.Label(inner, text="空闲", bg=PANEL, fg=MUT, font=("Segoe UI", 9))
        self.dev_stat.pack(anchor="w", pady=(6, 2))
        self.dev_log = tk.Text(inner, bg=BG, fg=MUT, height=7, relief="flat",
                               font=("Consolas", 9), wrap="word")
        self.dev_log.pack(fill="both", expand=True)
        self._dev_fill()

    def _dev_fill(self):
        key = self.dev_keys[self.dev_cmb.current()]
        self.dev_task_var.set(key)
        self.dev_params.delete(0, "end")
        self.dev_params.insert(0, self.DEV_DEFAULTS.get(key, "{}"))

    def on_dev_run(self):
        key = self.dev_keys[self.dev_cmb.current()]
        try:
            params = json.loads(self.dev_params.get() or "{}")
        except Exception:
            self.dev_stat.config(text="参数 JSON 格式错误", fg=R)
            return
        resources = {k: bool(v.get()) for k, v in self.res_vars.items()}
        ok, msg = self.devacc.start(key, params, resources)
        if not ok:
            self.dev_stat.config(text="启动失败：%s" % msg, fg=R)

    def on_dev_cancel(self):
        self.devacc.cancel()

    def _card_output(self, parent):
        outer = tk.Frame(parent, bg=BG)
        outer.pack(fill="both", expand=True, pady=4)
        lf = tk.Frame(outer, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        lf.pack(side="left", fill="both", expand=True, padx=(0, 6))
        tk.Label(lf, text="结果 / Result", bg=PANEL, fg=TXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self.txt_result = tk.Text(lf, bg=BG, fg=MUT, height=8, relief="flat",
                                  font=("Consolas", 9), wrap="word")
        self.txt_result.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        rf = tk.Frame(outer, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        rf.pack(side="right", fill="both", expand=True, padx=(6, 0))
        tk.Label(rf, text="调度日志 / Log", bg=PANEL, fg=TXT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self.txt_log = tk.Text(rf, bg=BG, fg=MUT, height=8, relief="flat",
                               font=("Consolas", 9), wrap="word")
        self.txt_log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ════════════════════════════════════════════════════════════════
    #  绘制助手
    # ════════════════════════════════════════════════════════════════
    def _draw_bar(self, cv, x, y, w, label, pct, color, valtext=None):
        pct = max(0, min(100, pct or 0))
        cv.create_text(x, y + 7, text=label, anchor="w", fill=MUT,
                       font=("Segoe UI", 9))
        bx = x + 62
        bw = w - 130
        cv.create_rectangle(bx, y, bx + bw, y + 14, fill=PANEL2, outline="")
        cv.create_rectangle(bx, y, bx + bw * pct / 100, y + 14, fill=color, outline="")
        cv.create_text(bx + bw + 8, y + 7, text=valtext or ("%.0f%%" % pct),
                       anchor="w", fill=MUT, font=("Segoe UI", 9))

    def _draw_hw(self):
        cv = self.cv_hw
        cv.delete("all")
        u = self.state["util"]
        w = cv.winfo_width() or 440
        y = 4
        self._draw_bar(cv, 0, y, w, "CPU", u.get("cpu", 0), lvl_color(u.get("cpu", 0))); y += 22
        self._draw_bar(cv, 0, y, w, "内存", u.get("mem", 0), lvl_color(u.get("mem", 0))); y += 22
        self._draw_bar(cv, 0, y, w, "磁盘", u.get("disk", 0), lvl_color(u.get("disk", 0))); y += 22
        for g in (u.get("gpus") or []):
            self._draw_bar(cv, 0, y, w, "GPU%d" % g["index"], g["gpu"], ACC2); y += 22
        # 网络
        cv.create_text(0, y + 7, text="网络", anchor="w", fill=MUT, font=("Segoe UI", 9))
        cv.create_text(62, y + 7, text="↑%.0f  ↓%.0f KB/s" % (
            u.get("net_kbps_up", 0), u.get("net_kbps_down", 0)),
            anchor="w", fill=MUT, font=("Segoe UI", 9)); y += 22
        # 每核
        per = u.get("per_cpu") or []
        if per:
            cv.create_text(0, y + 7, text="每核", anchor="w", fill=MUT, font=("Segoe UI", 9))
            bx = 62; bw = (w - 80) / max(1, len(per))
            for p in per:
                h = 14 * (p / 100.0)
                cv.create_rectangle(bx, y + 14 - h, bx + bw - 2, y + 14,
                                    fill=lvl_color(p), outline="")
                bx += bw

    def _draw_ratio(self, devices):
        cv = self.cv_ratio
        cv.delete("all")
        w = cv.winfo_width() or 900
        h = 40
        if not devices:
            cv.create_text(w / 2, h / 2, text="尚未评估", fill=MUT, font=("Segoe UI", 10))
            self.lb_legend.config(text="点「重新评估算力」开始")
            return
        x = 0
        legend = []
        for i, (did, d) in enumerate(devices.items()):
            seg = w * d["ratio"]
            color = SEG_COLORS[i % len(SEG_COLORS)]
            cv.create_rectangle(x, 0, x + seg, h, fill=color, outline=PANEL)
            if seg > 60:
                cv.create_text(x + seg / 2, h / 2,
                               text="%s %.0f%%" % (d["name"], d["ratio"] * 100),
                               fill="#06101f", font=("Segoe UI", 9, "bold"))
            x += seg
            legend.append("● %s：%.1f%% · 评分 %.2f" % (d["name"], d["ratio"] * 100, d["score"]))
        self.lb_legend.config(text="    ".join(legend))

    def _draw_progress(self, run):
        cv = self.cv_prog
        cv.delete("all")
        w = cv.winfo_width() or 700
        h = 22
        pct = (run.get("progress", 0) * 100) if run else 0
        cv.create_rectangle(0, 0, w, h, fill=PANEL2, outline="")
        cv.create_rectangle(0, 0, w * pct / 100, h, fill=ACC, outline="")
        cv.create_text(w / 2, h / 2, text="%.0f%%" % pct, fill=TXT,
                       font=("Segoe UI", 9, "bold"))
        # 任务流
        fcv = self.cv_flow
        fcv.delete("all")
        devs = (run or {}).get("devices", {})
        fcv.configure(height=max(10, len(devs) * 24))
        fw = fcv.winfo_width() or 880
        y = 2
        for did, d in devs.items():
            assigned = d.get("assigned", 0); done = d.get("done", 0)
            p = (done / assigned) if assigned else 0
            ic = "💻" if d.get("kind") == "pc" else "📱"
            fcv.create_text(0, y + 8, text="%s %s" % (ic, d.get("name", did)),
                            anchor="w", fill=TXT, font=("Segoe UI", 9))
            bx = 130; bw = fw - 230
            fcv.create_rectangle(bx, y, bx + bw, y + 16, fill=PANEL2, outline="")
            col = ACC if d.get("kind") == "pc" else G
            fcv.create_rectangle(bx, y, bx + bw * p, y + 16, fill=col, outline="")
            fcv.create_text(bx + bw + 8, y + 8, text="%d/%d" % (done, assigned),
                            anchor="w", fill=MUT, font=("Segoe UI", 9))
            y += 24

    # ════════════════════════════════════════════════════════════════
    #  事件
    # ════════════════════════════════════════════════════════════════
    def _fill_params(self):
        i = self.cmb.current()
        p = self.catalog[i]
        self.ent_params.delete(0, "end")
        self.ent_params.insert(0, json.dumps(p["params"], ensure_ascii=False))
        self.lb_desc.config(text=p["desc"])

    def on_assess(self):
        if self.assessing:
            return
        self.assessing = True
        self.btn_assess.config(text="评估中…", state="disabled")

        def work():
            try:
                cap = self.assessor.assess(force=True)
                self.last_ratio = cap["devices"]
            except Exception as e:
                self.last_ratio = {}
                self.root.after(0, lambda: self.lb_legend.config(text="评估失败：%s" % e))
            finally:
                self.assessing = False
                self.root.after(0, lambda: self.btn_assess.config(
                    text="重新评估算力", state="normal"))
        threading.Thread(target=work, daemon=True).start()

    def on_run(self):
        try:
            params = json.loads(self.ent_params.get() or "{}")
        except Exception:
            self.lb_desc.config(text="参数 JSON 格式错误", fg=R)
            return
        key = self.proj_keys[self.cmb.current()]
        ok, msg = self.scheduler.start(key, params)
        if not ok:
            self.lb_desc.config(text="启动失败：%s" % msg, fg=R)

    def on_cancel(self):
        self.scheduler.cancel()

    # ════════════════════════════════════════════════════════════════
    #  后台轮询 + 前台刷新
    # ════════════════════════════════════════════════════════════════
    def _poller(self):
        """后台线程：读硬件利用率(含0.2s阻塞) + 协调端状态。不碰 Tk。"""
        while not self._poll_stop:
            try:
                self.state["util"] = hardware.utilization()
                self.state["coord"] = self.client.status()
            except Exception:
                pass
            time.sleep(1.0)

    def _refresh(self):
        # 顶栏
        coord = self.state.get("coord")
        on = coord is not None
        self.lb_coord.config(text=("● 协调端在线 " if on else "○ 协调端离线 ") + self.cfg["coordinator_url"],
                             fg=(G if on else R))
        self.lb_lan.config(text="本机 %s:%d" % (lan_ip(), int(self.cfg["boss_port"])))

        # 硬件
        try:
            self._draw_hw()
        except Exception:
            pass

        # 手机列表
        phones = []
        if coord:
            for did, d in (coord.get("devices") or {}).items():
                if d.get("kind") == "phone" and d.get("online"):
                    phones.append((did, d))
        for wdg in self._phone_widgets:
            wdg.destroy()
        self._phone_widgets = []
        if phones:
            self.lb_phone_empty.pack_forget()
            for did, d in phones:
                r = d.get("resources") or {}
                lv = d.get("level", "?")
                lvcol = {"GREEN": G, "YELLOW": Y, "RED": R}.get(lv, MUT)
                fr = tk.Frame(self.fr_phones, bg=PANEL2)
                fr.pack(fill="x", pady=2)
                tk.Label(fr, text=d.get("name", did), bg=PANEL2, fg=TXT,
                         font=("Segoe UI", 9, "bold")).pack(side="left", padx=8, pady=4)
                tk.Label(fr, text=lv, bg=PANEL2, fg=lvcol,
                         font=("Segoe UI", 8)).pack(side="left", padx=4)
                cpu_disp = r.get("cpu_proc") or r.get("cpu") or 0   # 优先工人进程CPU(修永远0)
                info = "%s核 · CPU%.0f%%" % (r.get("cores", "?"), cpu_disp)
                if r.get("battery") is not None:
                    info += " · 🔋%s%%%s" % (r["battery"], "⚡" if r.get("charging") else "")
                b = (coord.get("billing") or {}).get(did, {}) if coord else {}
                if b.get("credits") is not None:
                    info += " · 工分%.1f 信誉%.0f" % (b.get("credits", 0), b.get("reputation", 100))
                    if b.get("checks_failed"):
                        info += "(失败%d)" % b["checks_failed"]
                tk.Label(fr, text=info, bg=PANEL2, fg=MUT,
                         font=("Segoe UI", 8)).pack(side="left", padx=4)
                self._phone_widgets.append(fr)
        else:
            self.lb_phone_empty.config(
                text="暂无手机接入。手机算力 app 填本机 IP\n%s:9000（协调端）即可。" % lan_ip())
            self.lb_phone_empty.pack(anchor="w")

        # 运行快照
        run = self.scheduler.run.snapshot() if self.scheduler.run else None
        busy = self.scheduler.is_busy()
        self.btn_run.config(state="disabled" if busy else "normal")
        self.btn_cancel.config(state="normal" if busy else "disabled")

        # 比例：运行中用 run 的，否则用上次评估的
        ratio_devs = (run or {}).get("devices") if run and run.get("devices") else self.last_ratio
        try:
            self._draw_ratio(ratio_devs or {})
        except Exception:
            pass

        # 进度
        try:
            self._draw_progress(run)
        except Exception:
            pass
        if run:
            st = run.get("status", "idle")
            label = {"preparing": "准备", "assessing": "评估算力", "running": "运行中",
                     "done": "完成 ✓", "error": "出错", "cancelled": "已取消",
                     "idle": "空闲"}.get(st, st)
            extra = ""
            if run.get("elapsed_s"):
                extra = "  ·  %s/%s 片  ·  %ss  ·  %s 片/秒" % (
                    run.get("done_items", 0), run.get("total_items", 0),
                    run.get("elapsed_s", 0), run.get("throughput", 0))
            self.lb_runstat.config(text="状态：%s%s" % (label, extra),
                                   fg=(G if st in ("running", "done") else
                                       R if st == "error" else MUT))
            # 结果 / 日志
            self._settext(self.txt_result, json.dumps(run.get("result"), ensure_ascii=False,
                                                      indent=2) if run.get("result") else "—")
            self._settext(self.txt_log, "\n".join(
                "[%ss] %s" % (l["t"], l["msg"]) for l in run.get("log", [])) or "—")

        # 全局模式 / 一体机
        try:
            self._refresh_global()
        except Exception:
            pass
        # 开发任务加速面板
        try:
            self._refresh_dev()
        except Exception:
            pass

        self.root.after(500, self._refresh)

    def _refresh_dev(self):
        c = self.devacc.caps()
        self.dev_caps_lb.config(text="本机: %s · %s · pip · 下载" % (
            "✓构建APK" if c.get("build") else "✗无SDK",
            "✓adb" if c.get("adb") else "✗无adb"))
        s = self.devacc.snapshot()
        st = s.get("status", "idle")
        busy = self.devacc.is_busy()
        self.btn_dev.config(state="disabled" if busy else "normal")
        self.btn_dev_cancel.config(state="normal" if busy else "disabled")
        label = {"idle": "空闲", "running": "运行中", "done": "完成 ✓",
                 "error": "出错", "cancelled": "已取消"}.get(st, st)
        m = s.get("metrics", {})
        parts = []
        if s.get("elapsed_s"):
            parts.append("%ss" % s["elapsed_s"])
        for k, fmt in [("download_s", "下载%ss"), ("install_s", "安装%ss"),
                       ("wall_s", "构建%ss"), ("files", "下载%d个"),
                       ("apks", "APK×%d"), ("wheelhouse_files", "wheelhouse%d")]:
            if m.get(k) is not None:
                parts.append(fmt % m[k])
        self.dev_stat.config(text="状态：%s  %s" % (label, "  ".join(parts)),
                             fg=(G if st in ("running", "done") else R if st == "error" else MUT))
        self._settext(self.dev_log, "\n".join(s.get("log", [])) or "—")

    def _settext(self, w, s):
        if w.get("1.0", "end-1c") == s:
            return
        w.delete("1.0", "end")
        w.insert("1.0", s)


def main():
    root = tk.Tk()
    BossGUI(root)
    root.mainloop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
