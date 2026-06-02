# -*- coding: utf-8 -*-
"""
WM ARM 机器码容器服务 (ARM Container Service) · 跑在中央服务器(算力老板)上
========================================================================
把古董 Windows Mobile 设备的"机器码执行"搬到中央 x86：每个 WM 版本(2003→6.5)一个
**容器**，给定配额(默认 1 核 / 1GB 内存 / 共享 GPU 3070)，统一生命周期管理 + GUI。

★诚实边界(重要)：
  真正的 "ARM→x86 机器码翻译 + WinCE API 垫片" 是多年级工程，本文件**不实现翻译器本身**。
  本文件实现的是**容器管理面**：版本清单 / 配额 / 启停 / 状态 / 资源限制(限核)。
  **执行后端可插拔**：
    - "sim"  (默认)：只管理状态与配额，不真正执行机器码——管理面就绪、执行面待接入。
    - "qemu" / "devemu"：配置 arm_emulator 指向 qemu-system-arm 或微软 DeviceEmulator，
      即由它真正跑 WM 镜像；本服务负责按配额限到 1 核并管理进程。
  这样蓝图落成"可运行的骨架"，而不是又一个空壳；接上真实模拟器即变真容器。
"""
import os
import time
import subprocess

# 支持的 WM 版本：从 2003 一直做到 6.5
WM_VERSIONS = ["2003", "2003SE", "5.0", "6.0", "6.1", "6.5"]
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class ArmContainer:
    def __init__(self, ver, cfg):
        self.ver = ver
        self.cores = int(cfg.get("arm_cores", 1))          # 默认单核
        self.mem_mb = int(cfg.get("arm_mem_mb", 1024))     # 默认 1GB
        self.gpu_share = bool(cfg.get("arm_gpu_share", True))  # 共享 GPU(3070)算力
        self.backend = cfg.get("arm_backend", "sim")
        self.emu_path = cfg.get("arm_emulator", "")
        # 每版本镜像路径(可选)：arm_image_2003 / arm_image_6_5 ...
        self.image = cfg.get("arm_image_" + ver.replace(".", "_"), "")
        self.state = "stopped"          # stopped | running | error
        self.pid = None
        self.started = None
        self.note = ""
        self._proc = None

    def start(self):
        if self.state == "running":
            return True, "已在运行"
        if self.backend == "sim":
            self.state = "running"
            self.started = time.time()
            self.note = "sim 后端：配额/状态已就绪；真实机器码执行需接入 ARM 模拟器后端"
            return True, "sim 启动"
        # 真实后端：起 ARM 模拟器(qemu-system-arm / 微软 DeviceEmulator)，按配额限 1 核
        exe = self.emu_path
        if not exe or not os.path.exists(exe):
            self.state = "error"
            self.note = "未配置 arm_emulator(qemu-system-arm 或 DeviceEmulator.exe 路径)"
            return False, self.note
        try:
            args = [exe]
            if self.image:
                args += (["/CEImage:" + self.image] if exe.lower().endswith("deviceemulator.exe")
                         else ["-kernel", self.image])
            self._proc = subprocess.Popen(args, creationflags=NO_WINDOW)
            self.pid = self._proc.pid
            self.state = "running"
            self.started = time.time()
            self.note = "真实后端 %s" % os.path.basename(exe)
            self._limit_cores()
            return True, "已启动 pid=%d" % self.pid
        except Exception as e:
            self.state = "error"
            self.note = str(e)
            return False, str(e)

    def _limit_cores(self):
        """限到 self.cores 个核(默认1核)：设进程 ProcessorAffinity。"""
        if not self.pid:
            return
        mask = (1 << self.cores) - 1   # 1核→0b1, 2核→0b11
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-Process -Id %d).ProcessorAffinity=%d" % (self.pid, mask)],
                           creationflags=NO_WINDOW, timeout=5,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def stop(self):
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None
        self.pid = None
        self.state = "stopped"
        self.started = None
        return True, "已停止"

    def status(self):
        up = round(time.time() - self.started, 1) if self.started else 0
        return {"ver": self.ver, "state": self.state, "cores": self.cores,
                "mem_mb": self.mem_mb, "gpu_share": self.gpu_share,
                "backend": self.backend, "pid": self.pid, "uptime_s": up,
                "note": self.note}


class ArmService:
    """中央服务器上的 ARM 容器总管：WM 2003→6.5 各一个容器。"""
    def __init__(self, cfg):
        self.cfg = cfg
        self.containers = {v: ArmContainer(v, cfg) for v in WM_VERSIONS}

    def list(self):
        return [c.status() for c in self.containers.values()]

    def start(self, ver):
        if ver == "*":
            for c in self.containers.values():
                c.start()
            return True, "全部启动"
        c = self.containers.get(ver)
        return c.start() if c else (False, "未知版本: %s" % ver)

    def stop(self, ver):
        if ver == "*":
            for c in self.containers.values():
                c.stop()
            return True, "全部停止"
        c = self.containers.get(ver)
        return c.stop() if c else (False, "未知版本: %s" % ver)

    def summary(self):
        running = sum(1 for c in self.containers.values() if c.state == "running")
        return {"running": running, "total": len(self.containers),
                "cores_each": int(self.cfg.get("arm_cores", 1)),
                "mem_each_mb": int(self.cfg.get("arm_mem_mb", 1024)),
                "gpu_share": bool(self.cfg.get("arm_gpu_share", True)),
                "backend": self.cfg.get("arm_backend", "sim")}
