# -*- coding: utf-8 -*-
"""
开发任务加速 (Dev Acceleration) · beta0.2
========================================
把 PyCharm 里"很吃算力/很慢"的开发操作，用局域网算力池加速：

  parallel_compute  并行计算   —— 把计算型项目按算力比例分到 PC+手机（走 scheduler）
  install_pip       装库        —— 多包【并行下载】到共享 wheelhouse，再离线安装（勾"网络"则手机也帮下）
  install_plugin    装插件      —— 一批插件/依赖 URL【分发到池里并行下载】（PC+手机一起）
  build_apk         构建 APK    —— Gradle【共享构建缓存 + 并行】构建，实时日志（仅 PC，手机无 SDK）
  deploy_apk        一键装真机  —— adb install -r 到 USB/无线连接的真机
  update_workers    更新工人app —— adb 把最新 worker APK 推装到所有连接的设备

资源勾选(resources)：{cpu,gpu,mem,disk,net} 控制行为——
  net=并行下载时把任务分到池里(含手机)；disk=用共享缓存(wheelhouse/gradle)；
  cpu=构建用 --parallel；gpu=计算优先用 GPU 后端。

★诚实边界：手机无 Android SDK/JDK，不能编译 APK；能真正帮上忙的是【并行下载】(贡献带宽)
 与【并行计算】。构建/部署落在有 SDK 的 PC / adb 连接的真机上。
"""
import os
import sys
import time
import glob
import shutil
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from cmdrunner import CommandRun


def _short(u, n=46):
    return u if len(u) <= n else u[:n - 1] + "…"


class DevAccelerator:
    def __init__(self, cfg, client, scheduler):
        self.cfg = cfg
        self.client = client
        self.scheduler = scheduler
        self.cache_dir = cfg.get("cache_dir") or os.path.join(os.path.dirname(__file__), "cache")
        self.wheel_dir = os.path.join(self.cache_dir, "wheels")
        self.plugin_dir = os.path.join(self.cache_dir, "plugins")
        self.gradle_home = cfg.get("gradle_home") or os.path.join(self.cache_dir, "gradle")
        for d in (self.cache_dir, self.wheel_dir, self.plugin_dir, self.gradle_home):
            os.makedirs(d, exist_ok=True)
        self.state = None
        self._cur = None          # 当前 CommandRun（用于取消/取日志）
        self._thread = None
        self._cancel = False

    # ── 能力探测 ──────────────────────────────────────────────────────────────
    def adb(self):
        return self.cfg.get("adb_path") or shutil.which("adb") or ""

    def caps(self):
        has_gradle = bool(shutil.which("gradle") or os.environ.get("ANDROID_HOME")
                          or os.environ.get("ANDROID_SDK_ROOT")
                          or (self.cfg.get("apk_project_dir") and os.path.exists(
                              os.path.join(self.cfg["apk_project_dir"], "gradlew.bat"))))
        return {"python": True, "download": True, "build": has_gradle,
                "adb": bool(self.adb())}

    # ── 生命周期 ──────────────────────────────────────────────────────────────
    def is_busy(self):
        # 调度器在跑(并行计算,任一入口) → 忙
        if self.scheduler.is_busy():
            return True
        # 命令型任务在跑 → 忙；compute 型完成后由 scheduler 状态决定(上面已判)
        if self.state and self.state.get("mode") == "cmd":
            return self.state.get("status") == "running"
        return False

    def cancel(self):
        self._cancel = True
        self.scheduler.cancel()
        if self._cur:
            self._cur.cancel()

    def start(self, task, params, resources=None):
        if self.is_busy():
            return False, "已有任务在运行"
        self._cancel = False
        self._cur = None
        resources = resources or {"cpu": True, "gpu": False, "mem": True, "disk": True, "net": True}
        # 并行计算直接交给 scheduler（CPU/GPU 资源分配）
        if task == "parallel_compute":
            proj = params.get("project", "prime_scan")
            ok, msg = self.scheduler.start(proj, params.get("params", {}))
            if ok:
                self.state = {"task": task, "mode": "compute", "resources": resources,
                              "status": "running", "started": time.time(), "log": [], "metrics": {}}
            return ok, msg
        # 其余是命令型开发任务
        self.state = {"task": task, "mode": "cmd", "resources": resources, "status": "running",
                      "started": time.time(), "log": [], "metrics": {}}
        self._thread = threading.Thread(target=self._run, args=(task, params, resources), daemon=True)
        self._thread.start()
        return True, "started"

    def snapshot(self):
        if not self.state:
            return {"status": "idle"}
        s = dict(self.state)
        if s.get("mode") == "compute":
            run = self.scheduler.run.snapshot() if self.scheduler.run else {}
            s["status"] = run.get("status", s.get("status"))
            s["compute"] = run
            s["log"] = ["[%ss] %s" % (l["t"], l["msg"]) for l in run.get("log", [])]
        s["elapsed_s"] = round((self.state.get("finished") or time.time()) - self.state["started"], 1)
        s["cache_dir"] = self.cache_dir
        s["log"] = (s.get("log") or [])[-180:]
        return s

    # ── 内部助手 ──────────────────────────────────────────────────────────────
    def _log(self, msg):
        self.state["log"] = (self.state.get("log") or [])
        self.state["log"].append(msg)
        self.state["log"] = self.state["log"][-300:]

    def _finish(self, status, **metrics):
        self.state["status"] = status
        self.state["finished"] = time.time()
        self.state["metrics"].update(metrics)

    def _runcmd(self, argv, cwd=None, env=None):
        self._cur = CommandRun(argv, cwd=cwd, env=env, on_line=self._log)
        self._cur.run()
        return self._cur.exit_code

    def _run(self, task, params, resources):
        try:
            fn = {"install_pip": self._install_pip, "install_plugin": self._install_plugin,
                  "prefetch": self._prefetch, "build_apk": self._build_apk,
                  "deploy_apk": self._deploy_apk, "update_workers": self._update_workers
                  }.get(task)
            if not fn:
                self._log("未知任务: %s" % task)
                self._finish("error")
                return
            fn(params, resources)
        except Exception as e:
            self._log("出错: %s" % e)
            self._finish("error")

    # ── 池内并行下载（网络共享，含手机）──────────────────────────────────────
    def _pool_download(self, urls, dest):
        """把一批 URL 分发到算力池(含手机)并行下载；协调端不可用则本机并行下。
        返回 {url: {worker, size, kbps,...}}。"""
        os.makedirs(dest, exist_ok=True)
        done = {}
        use_pool = self.client.online()
        if use_pool:
            self._log("网络共享：%d 个文件分发到算力池并行下载（PC+手机）…" % len(urls))
            jobmap = {}
            for u in urls:
                jid = self.client.submit("download", {"url": u}, weight="small", requires=["download"])
                if jid:
                    jobmap[jid] = u
                else:
                    use_pool = False
                    break
            deadline = time.time() + max(60, len(urls) * 25)
            while jobmap and not self._cancel and time.time() < deadline:
                res = self.client.job_results(list(jobmap.keys()))
                for jid, jb in list(res.items()):
                    if jb.get("status") == "done":
                        u = jobmap.pop(jid)
                        r = jb.get("result") or {}
                        done[u] = {"worker": jb.get("worker", "?"), **r}
                        self._log("  ✓ %s  by %s  %.0fKB/s  %.1fMB" % (
                            _short(u), done[u]["worker"], r.get("kbps", 0),
                            (r.get("size", 0) or 0) / 1048576.0))
                time.sleep(1.0)
            # 池里没下完的，本机兜底
            leftover = list(jobmap.values())
            if leftover:
                self._log("池内 %d 个超时/未完，本机兜底下载…" % len(leftover))
                self._local_download(leftover, dest, done)
        else:
            self._log("协调端不可用，本机并行下载 %d 个文件…" % len(urls))
            self._local_download(urls, dest, done)
        return done

    def _local_download(self, urls, dest, done):
        def dl(u):
            try:
                fn = os.path.join(dest, os.path.basename(u.split("?")[0]) or "file")
                t0 = time.time()
                urllib.request.urlretrieve(u, fn)
                sz = os.path.getsize(fn)
                ms = (time.time() - t0) * 1000
                done[u] = {"worker": "pc(local)", "size": sz, "ms": round(ms),
                           "kbps": round(sz / 1024 / max(0.001, ms / 1000), 1)}
                self._log("  ✓ %s  本机  %.0fKB/s" % (_short(u), done[u]["kbps"]))
            except Exception as e:
                done[u] = {"worker": "pc(local)", "error": str(e)}
                self._log("  ✗ %s  %s" % (_short(u), e))
        with ThreadPoolExecutor(max_workers=min(8, max(2, len(urls)))) as ex:
            list(ex.map(dl, urls))

    # ── 装库 (pip) ────────────────────────────────────────────────────────────
    def _install_pip(self, params, resources):
        py = params.get("python") or sys.executable
        pkgs = []
        req = params.get("requirements")
        if req and os.path.exists(req):
            for line in open(req, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#"):
                    pkgs.append(line)
        raw = params.get("packages")
        if isinstance(raw, str):
            pkgs += [x.strip() for x in raw.replace(",", " ").split() if x.strip()]
        elif isinstance(raw, list):
            pkgs += raw
        if not pkgs:
            self._log("没有要安装的包（填 packages 或 requirements 路径）")
            self._finish("error")
            return
        use_cache = resources.get("disk", True)
        self._log("装库：%d 个包 → %s并行下载 → 离线安装" % (
            len(pkgs), "共享 wheelhouse " if use_cache else ""))
        # 1) 并行下载到 wheelhouse（多个 pip download 子进程并发；磁盘缓存命中即秒下）
        t0 = time.time()
        workers = min(8, max(2, (os.cpu_count() or 4)))

        def dl(pkg):
            c = CommandRun([py, "-m", "pip", "download", pkg, "-d", self.wheel_dir],
                           on_line=self._log)
            c.run()
            return pkg, c.exit_code
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(dl, pkgs))
        dl_s = round(time.time() - t0, 1)
        fails = [p for p, rc in results if rc != 0]
        self._log("下载阶段 %.1fs，失败 %d 个" % (dl_s, len(fails)))
        if self._cancel:
            self._finish("cancelled"); return
        # 2) 离线安装（命中 wheelhouse；缺的回退在线）
        t1 = time.time()
        rc = self._runcmd([py, "-m", "pip", "install", "--find-links", self.wheel_dir] + pkgs)
        inst_s = round(time.time() - t1, 1)
        wheels = len(glob.glob(os.path.join(self.wheel_dir, "*")))
        self._finish("done" if rc == 0 else "error",
                     download_s=dl_s, install_s=inst_s, wheelhouse_files=wheels,
                     packages=len(pkgs), failed=len(fails))

    # ── 装插件 / 预取（池内并行下载）─────────────────────────────────────────
    def _install_plugin(self, params, resources):
        self._prefetch(params, resources, dest=self.plugin_dir, label="装插件")

    def _prefetch(self, params, resources, dest=None, label="预取"):
        dest = dest or os.path.join(self.cache_dir, "prefetch")
        urls = params.get("urls")
        if isinstance(urls, str):
            urls = [u.strip() for u in urls.replace(",", " ").replace("\n", " ").split() if u.strip()]
        urls = urls or []
        if not urls:
            self._log("没有 URL（填 urls，多个用空格/逗号/换行分隔）")
            self._finish("error")
            return
        t0 = time.time()
        # 勾了"网络"才分发到池(含手机)；否则本机下
        if resources.get("net", True):
            done = self._pool_download(urls, dest)
        else:
            done = {}
            self._local_download(urls, dest, done)
        by_dev = {}
        total = 0
        for u, r in done.items():
            by_dev[r.get("worker", "?")] = by_dev.get(r.get("worker", "?"), 0) + 1
            total += r.get("size", 0) or 0
        ok = len([1 for r in done.values() if "error" not in r])
        self._log("%s 完成：%d/%d，合计 %.1fMB，缓存于 %s" % (
            label, ok, len(urls), total / 1048576.0, dest))
        self._finish("done" if ok else "error", files=ok, total_mb=round(total / 1048576.0, 1),
                     by_device=by_dev, pool_s=round(time.time() - t0, 1))

    # ── 构建 APK（Gradle 缓存 + 并行）────────────────────────────────────────
    def _build_apk(self, params, resources):
        proj = params.get("project_dir") or self.cfg.get("apk_project_dir") or ""
        gtask = params.get("task", "assembleDebug")
        if not proj or not os.path.isdir(proj):
            self._log("安卓工程目录无效：%s（填 project_dir，或在配置里设 apk_project_dir）" % proj)
            self._finish("error")
            return
        gw = os.path.join(proj, "gradlew.bat" if os.name == "nt" else "gradlew")
        if not os.path.exists(gw):
            g = shutil.which("gradle")
            if not g:
                self._log("没找到 gradlew 也没找到 gradle")
                self._finish("error")
                return
            argv = [g, gtask]
        else:
            argv = [gw, gtask]
        cold = not os.path.exists(os.path.join(self.gradle_home, "caches"))
        if resources.get("cpu", True):
            argv.append("--parallel")
        if resources.get("disk", True):
            argv += ["--build-cache", "-g", self.gradle_home]   # 共享构建缓存
        argv.append("--console=plain")
        env = dict(os.environ)
        if resources.get("disk", True):
            env["GRADLE_USER_HOME"] = self.gradle_home
        self._log("Gradle 构建 %s（%s%s）" % (
            gtask, "冷构建" if cold else "缓存可命中",
            " · 并行" if resources.get("cpu", True) else ""))
        t0 = time.time()
        rc = self._runcmd(argv, cwd=proj, env=env)
        wall = round(time.time() - t0, 1)
        apks = glob.glob(os.path.join(proj, "**", "outputs", "apk", "**", "*.apk"), recursive=True)
        if rc == 0:
            self._log("✓ 构建成功 %.1fs，产物：%s" % (
                wall, ", ".join(os.path.relpath(a, proj) for a in apks) or "(未找到)"))
            self.state["metrics"]["apk_paths"] = apks
            self._finish("done", wall_s=wall, cold=cold, apks=len(apks))
        else:
            self._finish("error", wall_s=wall, cold=cold)

    # ── 一键部署到真机（adb）──────────────────────────────────────────────────
    def _deploy_apk(self, params, resources):
        adb = self.adb()
        if not adb:
            self._log("没找到 adb（在配置 adb_path 指定，或装 platform-tools 并加 PATH）")
            self._finish("error")
            return
        apk = params.get("apk")
        if not apk:
            # 没指定就找工程最新产物
            proj = params.get("project_dir") or self.cfg.get("apk_project_dir") or ""
            cands = glob.glob(os.path.join(proj, "**", "outputs", "apk", "**", "*.apk"), recursive=True)
            apk = max(cands, key=os.path.getmtime) if cands else ""
        if not apk or not os.path.exists(apk):
            self._log("找不到 APK（填 apk 路径，或先构建）")
            self._finish("error")
            return
        self._log("已连接设备：")
        self._runcmd([adb, "devices"])
        self._log("安装 %s …" % os.path.basename(apk))
        rc = self._runcmd([adb, "install", "-r", apk])
        self._finish("done" if rc == 0 else "error", apk=os.path.basename(apk))

    # ── 更新工人 app（adb 推装最新 worker APK）───────────────────────────────
    def _update_workers(self, params, resources):
        adb = self.adb()
        if not adb:
            self._log("没找到 adb；无法自动推装工人 app")
            self._finish("error")
            return
        apk = params.get("worker_apk") or self.cfg.get("worker_apk_path") or ""
        if not apk or not os.path.exists(apk):
            self._log("没有工人 APK（配置 worker_apk_path 指向最新构建的 worker app APK）")
            self._finish("error")
            return
        self._log("连接的设备：")
        self._runcmd([adb, "devices"])
        self._log("向所有连接设备推装最新工人 app：%s" % os.path.basename(apk))
        rc = self._runcmd([adb, "install", "-r", apk])
        self._log("提示：纯局域网静默更新需 worker app 内置升级检查；当前用 adb 推装。")
        self._finish("done" if rc == 0 else "error")
