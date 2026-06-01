# -*- coding: utf-8 -*-
"""
调度器 (Scheduler) —— 老板的核心
================================
一次"加速运行"(Run)的全过程：

  1. 评估算力      capability.assess() → 各设备分数 + 分配比例 ratio。
  2. 拆分项目      project.make_items(params) → 一大批可并行 work-item。
  3. 按比例分配    按 ratio 把 item 切给 PC 和各手机（强的多分、弱的少分）。
  4. 并行派发      PC 那份 → 本地多核引擎(executor)；手机那份 → 提交到协调端队列，
                   手机 worker 自己来拉(work-stealing)。
  5. 边跑边收      实时更新每台设备的完成数 / 吞吐，供 UI 可视化。
  6. 兜底重算      手机分片若超时未完成(掉线/RED)，老板用 PC 把它补算掉，绝不卡死。
  7. 合并产出      project.reduce(all_results) → 项目最终结果。

同一时间只跑一个 Run（老板就一个）。Run 状态全程可被 UI 轮询。
"""
import time
import threading

import workloads


class Run:
    """一次加速运行的全部状态（被 UI 实时读取）。"""
    def __init__(self, project_key, params):
        self.project_key = project_key
        self.params = params
        self.status = "preparing"      # preparing|assessing|running|done|error|cancelled
        self.error = None
        self.total_items = 0
        self.ratio = {}                # device_id -> 0~1
        self.devices = {}              # device_id -> {name,kind,score,ratio,assigned,done}
        self.started = time.time()
        self.finished = None
        self.result = None
        self.log = []
        self._cancel = False

    def note(self, msg):
        self.log.append({"t": round(time.time() - self.started, 1), "msg": msg})
        self.log[:] = self.log[-50:]

    def snapshot(self):
        done = sum(d.get("done", 0) for d in self.devices.values())
        elapsed = (self.finished or time.time()) - self.started
        return {
            "project": self.project_key, "params": self.params,
            "status": self.status, "error": self.error,
            "total_items": self.total_items, "done_items": done,
            "progress": round(done / self.total_items, 4) if self.total_items else 0,
            "ratio": self.ratio, "devices": self.devices,
            "elapsed_s": round(elapsed, 1),
            "throughput": round(done / elapsed, 2) if elapsed > 0 else 0,
            "result": self.result, "log": self.log[-12:],
        }


class Scheduler:
    def __init__(self, cfg, client, assessor, executor, project_registry):
        self.cfg = cfg
        self.client = client
        self.assessor = assessor
        self.executor = executor
        self.projects = project_registry
        self.run = None
        self._thread = None
        self._lock = threading.Lock()

    def is_busy(self):
        return self.run is not None and self.run.status in ("preparing", "assessing", "running")

    def cancel(self):
        if self.run:
            self.run._cancel = True
            self.executor.cancel()

    def start(self, project_key, params):
        """启动一次运行（后台线程）。返回 (ok, msg)。"""
        if self.is_busy():
            return False, "已有运行在进行中"
        if project_key not in self.projects:
            return False, "未知项目: %s" % project_key
        self.run = Run(project_key, params)
        self._thread = threading.Thread(target=self._execute, daemon=True)
        self._thread.start()
        return True, "started"

    # ── 主流程 ────────────────────────────────────────────────────────────────
    def _execute(self):
        run = self.run
        project = self.projects[run.project_key]
        try:
            # 1) 评估算力 + 比例
            run.status = "assessing"
            run.note("评估各设备算力…")
            cap = self.assessor.assess()
            run.ratio = cap["ratio"]
            for did, d in cap["devices"].items():
                run.devices[did] = {"name": d["name"], "kind": d["kind"],
                                    "score": round(d["score"], 3), "ratio": round(d["ratio"], 4),
                                    "assigned": 0, "done": 0}
            ratio_txt = "，".join("%s %.0f%%" % (d["name"], d["ratio"] * 100)
                                 for d in run.devices.values())
            run.note("算力比例：" + (ratio_txt or "无设备"))

            # 2) 拆项目
            items = project.make_items(run.params)
            run.total_items = len(items)
            run.note("项目拆为 %d 个分片" % len(items))

            # 3) 按比例切分
            buckets = self._split(items, cap, project)
            run.status = "running"

            # 4) 并行派发：PC 本地线程 + 手机经协调端
            results = [None] * len(items)
            threads = []
            for did, idxs in buckets.items():
                run.devices[did]["assigned"] = len(idxs)
                if did == "pc":
                    t = threading.Thread(target=self._run_pc,
                                         args=(project, items, idxs, results, run), daemon=True)
                else:
                    t = threading.Thread(target=self._run_phone,
                                         args=(project, items, idxs, results, run, did), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join()

            if run._cancel:
                run.status = "cancelled"
                run.note("已取消")
            else:
                # 7) 合并
                run.result = project.reduce(results)
                run.status = "done"
                run.note("完成 ✓")
        except Exception as e:
            run.status = "error"
            run.error = str(e)
            run.note("出错：%s" % e)
        finally:
            run.finished = time.time()

    # ── 按比例切分 ────────────────────────────────────────────────────────────
    def _split(self, items, cap, project):
        """按 ratio 把 item 下标分给各设备。pc_only 项目全给 PC。"""
        n = len(items)
        devs = cap["devices"]
        # pc_only：全部给 PC（若没启用 PC，则没法跑，留空让上层报）
        phone_ids = [d for d in devs if devs[d]["kind"] == "phone" and project.phone_job(items[0]) is not None]
        pc_on = "pc" in devs
        buckets = {d: [] for d in devs}
        if not phone_ids or not pc_on:
            # 只有一种设备：全给它（优先 PC）
            target = "pc" if pc_on else (phone_ids[0] if phone_ids else None)
            if target is None:
                return {}
            buckets.setdefault(target, [])
            buckets[target] = list(range(n))
            return {k: v for k, v in buckets.items() if v}
        # 多设备：按比例分配整数配额（最大余数法），保证总和=n
        weights = {d: devs[d]["ratio"] for d in devs if (d == "pc" or d in phone_ids)}
        wsum = sum(weights.values()) or 1.0
        quota = {d: n * w / wsum for d, w in weights.items()}
        alloc = {d: int(q) for d, q in quota.items()}
        rem = n - sum(alloc.values())
        for d in sorted(weights, key=lambda x: quota[x] - int(quota[x]), reverse=True):
            if rem <= 0:
                break
            alloc[d] += 1
            rem -= 1
        # 交错分配下标（让各设备拿到的活均匀，不是前一段后一段）
        order = sorted(weights, key=lambda x: -alloc[x])
        i = 0
        for d in order:
            for _ in range(alloc[d]):
                buckets[d].append(i)
                i += 1
        return {k: v for k, v in buckets.items() if v}

    # ── PC 本地执行 ──────────────────────────────────────────────────────────
    def _run_pc(self, project, items, idxs, results, run):
        sub = [items[i] for i in idxs]
        # 本地模型真实后端：逐个跑（可能用 GPU）；否则走多核 executor 跑 sim/原语
        real = getattr(project, "real_pc_backend", lambda: None)()
        if real and run.project_key == "local_model":
            for k, i in enumerate(idxs):
                if run._cancel:
                    break
                results[i] = project.run_pc_item(items[i])
                run.devices["pc"]["done"] = k + 1
            return

        def on_prog(done, total, ccs):
            run.devices["pc"]["done"] = done
        out, _ = self.executor.run(project.workload_kind, sub, on_prog)
        for k, i in enumerate(idxs):
            results[i] = out[k]
        run.devices["pc"]["done"] = len(idxs)

    # ── 手机执行（经协调端）──────────────────────────────────────────────────
    def _run_phone(self, project, items, idxs, results, run, did):
        """把这台手机的分片逐个提交到协调端，轮询完成；超时则 PC 兜底。"""
        poll = float(self.cfg.get("poll_interval", 1.0))
        pending = {}      # job_id -> item index
        for i in idxs:
            jt, payload = project.phone_job(items[i])
            jid = self.client.submit(jt, payload, weight="small")
            if jid:
                pending[jid] = i
            else:
                results[i] = self._pc_fallback(project, items[i])  # 协调端不可用→PC兜底
                run.devices[did]["done"] += 1
        deadline = time.time() + max(30, len(idxs) * 8)   # 给手机充足时间
        while pending and not run._cancel:
            jobs = self.client.job_results(list(pending.keys()))
            for jid, jb in list(jobs.items()):
                if jb.get("status") == "done":
                    i = pending.pop(jid)
                    results[i] = project.phone_merge(items[i], jb.get("result") or {})
                    run.devices[did]["done"] += 1
            if not pending:
                break
            if time.time() > deadline:
                run.note("%s 有 %d 个分片超时，PC 兜底" % (run.devices[did]["name"], len(pending)))
                for jid, i in pending.items():
                    results[i] = self._pc_fallback(project, items[i])
                    run.devices[did]["done"] += 1
                pending.clear()
                break
            time.sleep(poll)

    def _pc_fallback(self, project, item):
        """手机掉链子时用 PC 本机直接把这个 item 算掉。"""
        if project.workload_kind == "model_sim":
            r = project.run_pc_item(item)
            return r if r is not None else workloads.run_item("model_sim", item)
        return workloads.run_item(project.workload_kind, item)
