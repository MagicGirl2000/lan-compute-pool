# -*- coding: utf-8 -*-
"""
流式命令运行器 (Streaming Command Runner)
========================================
开发任务（Gradle 构建、pip 装库、adb 部署）本质是跑外部命令并要看【实时日志】。
本模块封装：起子进程、行级捕获 stdout/stderr、回调推日志、可取消。

与算力任务的 ProcessPool 不同——那是并行算数，这是跑命令看输出。
"""
import subprocess
import threading
import time


class CommandRun:
    def __init__(self, argv, cwd=None, env=None, on_line=None):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.on_line = on_line
        self.lines = []
        self.status = "pending"    # pending|running|done|error
        self.exit_code = None
        self.started = None
        self.finished = None
        self._proc = None
        self._lock = threading.Lock()

    def _add(self, s):
        with self._lock:
            self.lines.append(s)
            if len(self.lines) > 600:
                self.lines = self.lines[-600:]
        if self.on_line:
            try:
                self.on_line(s)
            except Exception:
                pass

    def run(self):
        self.status = "running"
        self.started = time.time()
        self._add("$ " + " ".join(self.argv))
        try:
            self._proc = subprocess.Popen(
                self.argv, cwd=self.cwd, env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                bufsize=1, universal_newlines=True, encoding="utf-8", errors="replace")
            for line in self._proc.stdout:
                self._add(line.rstrip("\n"))
            self._proc.wait()
            self.exit_code = self._proc.returncode
            self.status = "done" if self.exit_code == 0 else "error"
        except FileNotFoundError:
            self._add("[找不到命令] %s" % self.argv[0])
            self.status = "error"
            self.exit_code = -1
        except Exception as e:
            self._add("[运行出错] %s" % e)
            self.status = "error"
            self.exit_code = -1
        finally:
            self.finished = time.time()

    def cancel(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def tail(self, n=160):
        with self._lock:
            return list(self.lines[-n:])

    def wall(self):
        if not self.started:
            return 0.0
        return round((self.finished or time.time()) - self.started, 1)
