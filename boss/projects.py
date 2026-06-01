# -*- coding: utf-8 -*-
"""
项目定义 (Projects)
==================
"老板"要加速的具体项目。每个项目知道三件事：
  1. make_items(params) -> [work-item,...]   把一个大项目拆成许多可并行的小 item。
  2. workload_kind                            这些 item 用哪个 workloads 原语执行
                                              （同时是派给手机 worker 的 job type）。
  3. reduce(results) -> dict                  把所有 item 的结果合并成项目最终结果。

调度器(scheduler)负责"按算力比例把这些 item 分给 PC 和手机"，项目本身不关心分给谁。

内置项目：
  local_model  本地模型推理（旗舰）：把一批 prompt 分发到各设备做推理。
  prime_scan   大区间素数统计
  hash_grind   批量 SHA-256
  montecarlo   蒙特卡洛估 π
"""
import workloads

# 安卓 worker(Worker.kt)原生支持的 job 类型——只有这几种能直接派给手机。
PHONE_NATIVE = {"prime", "hash", "compute"}


class Project:
    key = "base"
    title = "基础项目"
    workload_kind = "compute"   # PC 本地执行用的 workloads 原语
    pc_only = False             # True=该项目只在 PC 跑（手机 worker 无法正确计算）

    def make_items(self, params):
        raise NotImplementedError

    def reduce(self, results):
        return {"items": len([r for r in results if r is not None])}

    # ── 手机分片适配 ──────────────────────────────────────────────────────────
    def phone_job(self, item):
        """把一个 item 转成派给手机的 (job_type, payload)；返回 None=这台不能跑(留给PC)。
        默认：原生类型直接派，否则 None。"""
        if self.pc_only or self.workload_kind not in PHONE_NATIVE:
            return None
        return self.workload_kind, item

    def phone_merge(self, item, phone_result):
        """手机算完后，把它的原始结果整理成项目结果。默认原样返回。"""
        return phone_result


# ── 旗舰：本地部署模型 分布式推理 ────────────────────────────────────────────
class LocalModelProject(Project):
    """
    本地部署模型 —— 数据并行式分布式推理。
    把一批输入(prompt)切成 item，按算力比例分给 PC / 手机各自跑模型前向。

    后端(config.model_backend)：
      "sim"          零依赖模拟：用与 token 数成正比的算力代表一次推理（默认，开箱即跑）。
      "llama_cpp"    PC 端用 llama-cpp-python 加载 gguf 真模型跑（需自行 pip 安装+给模型路径）。
      "transformers" PC 端用 HuggingFace transformers 跑（需 torch，GPU 自动启用）。
    ★真实后端目前只在 PC 分片上生效；手机分片仍走 sim/代理负载，
     若要手机也跑真模型，需给安卓 worker 加同名 model 推理能力（README 有说明）。
    """
    key = "local_model"
    title = "本地模型推理（数据并行）"
    workload_kind = "model_sim"   # 给手机/本地模拟时用；真实后端在 run_pc_item 覆盖

    def __init__(self, cfg):
        self.cfg = cfg
        self._backend = None

    def make_items(self, params):
        prompts = params.get("prompts")
        if not prompts:
            # 没给就造一批演示 prompt
            n = int(params.get("count", 200))
            prompts = ["请用一句话解释概念 #%d。" % i for i in range(n)]
        max_tokens = int(params.get("max_tokens", 48))
        return [{"prompt": p, "max_tokens": max_tokens} for p in prompts]

    def reduce(self, results):
        ok = [r for r in results if r and "text" in r]
        toks = sum(r.get("tokens", 0) for r in ok)
        return {"replies": len(ok), "total_tokens": toks,
                "sample": ok[0]["text"] if ok else None}

    # 真实后端（仅 PC 分片调用；sim 时退回 workloads.w_model_sim）
    def real_pc_backend(self):
        be = self.cfg.get("model_backend", "sim")
        if be == "sim":
            return None
        if self._backend is not None:
            return self._backend
        path = self.cfg.get("model_path", "")
        try:
            if be == "llama_cpp":
                from llama_cpp import Llama
                self._backend = ("llama_cpp", Llama(model_path=path))
            elif be == "transformers":
                from transformers import pipeline
                self._backend = ("transformers", pipeline("text-generation", model=path))
        except Exception as e:
            print("[local_model] 真实后端加载失败，退回 sim：", e)
            self._backend = None
        return self._backend

    def run_pc_item(self, item):
        """PC 分片的单 item 执行：有真实后端就跑真模型，否则 None（让调用方用 sim）。"""
        be = self.real_pc_backend()
        if not be:
            return None
        kind, model = be
        prompt = item.get("prompt", "")
        mt = int(item.get("max_tokens", 48))
        try:
            if kind == "llama_cpp":
                out = model(prompt, max_tokens=mt)
                text = out["choices"][0]["text"]
            else:
                text = model(prompt, max_new_tokens=mt)[0]["generated_text"]
            return {"prompt": prompt, "tokens": mt, "text": text}
        except Exception as e:
            return {"prompt": prompt, "tokens": mt, "text": "[err]%s" % e}

    # 手机不会跑模型，但可以承担等价的 compute 重活：把推理折算成 compute 迭代派过去，
    # 手机算完重活后，老板这边只补上（极廉价的）回复文本——重计算真实地offload到了手机。
    def phone_job(self, item):
        return "compute", {"iterations": workloads.model_sim_cost(item)}

    def phone_merge(self, item, phone_result):
        return workloads.model_sim_text(item)


# ── 演示项目 ─────────────────────────────────────────────────────────────────
class PrimeScanProject(Project):
    key = "prime_scan"
    title = "大区间素数统计"
    workload_kind = "prime"

    def make_items(self, params):
        hi = int(params.get("limit", 2_000_000))
        chunk = int(params.get("chunk", 50_000))
        items = []
        lo = 2
        while lo < hi:
            items.append({"lo": lo, "hi": min(lo + chunk, hi)})
            lo += chunk
        return items

    def reduce(self, results):
        total = sum(r.get("primes", 0) for r in results if r)
        return {"primes_total": total, "shards": len(results)}


class HashGrindProject(Project):
    key = "hash_grind"
    title = "批量 SHA-256"
    workload_kind = "hash"

    def make_items(self, params):
        n = int(params.get("count", 400))
        rounds = int(params.get("rounds", 80_000))
        return [{"seed": "s%d" % i, "rounds": rounds} for i in range(n)]

    def reduce(self, results):
        return {"hashed": len([r for r in results if r]),
                "rounds_each": results[0].get("rounds") if results and results[0] else 0}


class MonteCarloProject(Project):
    key = "montecarlo"
    title = "蒙特卡洛估 π"
    workload_kind = "montecarlo"
    pc_only = True   # 安卓 worker 无 montecarlo 原语；要让手机也跑，需给 worker 加该类型

    def make_items(self, params):
        total = int(params.get("samples", 50_000_000))
        chunk = int(params.get("chunk", 1_000_000))
        items = []
        i = 0
        rem = total
        while rem > 0:
            s = min(chunk, rem)
            items.append({"samples": s, "seed": i + 1})
            rem -= s
            i += 1
        return items

    def reduce(self, results):
        inside = sum(r.get("inside", 0) for r in results if r)
        total = sum(r.get("samples", 0) for r in results if r)
        pi = 4.0 * inside / total if total else 0
        return {"samples": total, "inside": inside, "pi_estimate": round(pi, 6)}


def registry(cfg):
    """返回 {key: Project实例}。"""
    return {
        LocalModelProject.key: LocalModelProject(cfg),
        PrimeScanProject.key: PrimeScanProject(),
        HashGrindProject.key: HashGrindProject(),
        MonteCarloProject.key: MonteCarloProject(),
    }


def catalog(cfg):
    """给 UI 用的项目清单（含默认参数提示）。"""
    return [
        {"key": "local_model", "title": "本地模型推理（数据并行）",
         "params": {"count": 200, "max_tokens": 48},
         "desc": "把一批 prompt 按算力比例分发到 PC/手机做模型前向推理"},
        {"key": "prime_scan", "title": "大区间素数统计",
         "params": {"limit": 2000000, "chunk": 50000},
         "desc": "统计 [2,limit) 内素数个数，分片并行"},
        {"key": "hash_grind", "title": "批量 SHA-256",
         "params": {"count": 400, "rounds": 80000},
         "desc": "大量哈希链，CPU 密集、易分布式"},
        {"key": "montecarlo", "title": "蒙特卡洛估 π",
         "params": {"samples": 50000000, "chunk": 1000000},
         "desc": "撒点估 π，分片求和"},
    ]
