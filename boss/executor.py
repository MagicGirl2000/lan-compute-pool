# -*- coding: utf-8 -*-
"""
PC 本地执行引擎 (Local PC Executor)
==================================
让"电脑那一份"任务吃满 PC 的关键算力——用 multiprocessing 跨【全部物理核】并行，
这样 PC 作为算力池里最强的成员，能真正承担它按比例分到的大头。

  LocalExecutor.run(kind, items, on_progress) —— 把一批 work-item 丢进进程池并行算，
  边算边回调进度，返回每个 item 的结果列表（顺序与输入一致）。

GPU 说明：纯 Python 的逐元素负载放 GPU 反而慢；这里的 GPU"利用"留给真实模型后端
(LocalModelProject + torch/llama_cpp)去吃。本引擎专注把 CPU 多核吃满。
"""
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import workloads


def _task(args):
    kind, idx, item = args
    t0 = time.time()
    res = workloads.run_item(kind, item)
    return idx, res, time.time() - t0


class LocalExecutor:
    def __init__(self, max_workers=None):
        # 默认留一个核给系统/UI，避免整机卡死
        if max_workers is None:
            max_workers = max(1, (os.cpu_count() or 2) - 1)
        self.max_workers = max_workers
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self, kind, items, on_progress=None):
        """
        并行执行 items。on_progress(done, total, cpu_core_sec) 实时回调。
        返回 (results_list, stats)。results 顺序与 items 对齐。
        """
        self._cancel = False
        n = len(items)
        results = [None] * n
        cpu_core_sec = 0.0
        done = 0
        if n == 0:
            return results, {"cpu_core_sec": 0.0, "done": 0, "workers": self.max_workers}
        with ProcessPoolExecutor(max_workers=self.max_workers) as ex:
            futs = {ex.submit(_task, (kind, i, items[i])): i for i in range(n)}
            for fut in as_completed(futs):
                if self._cancel:
                    break
                try:
                    idx, res, dt = fut.result()
                    results[idx] = res
                    cpu_core_sec += dt
                except Exception as e:
                    results[futs[fut]] = {"error": str(e)}
                done += 1
                if on_progress:
                    on_progress(done, n, cpu_core_sec)
        return results, {"cpu_core_sec": round(cpu_core_sec, 3),
                         "done": done, "workers": self.max_workers}
