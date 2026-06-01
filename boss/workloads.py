# -*- coding: utf-8 -*-
"""
工作负载原语 (Workload Primitives)
=================================
真正"干活"的纯函数。每个函数处理一个 work-item，返回该 item 的结果。

为什么单独成文件、且都是顶层函数？
  Windows 上 multiprocessing 用 spawn，子进程要能 import 并 pickle 这些函数，
  所以它们必须是模块顶层、无闭包、参数可序列化。

这些原语同时是"基准测试"和"项目分片"的执行体；手机 worker(Android Worker.kt)里
有同名的 prime/hash/compute 实现，保证 PC 与手机跑的是【可比】的活，能力比例才有意义。
"""
import hashlib
import math


def w_prime(item):
    """统计 [item['lo'], item['hi']) 区间内的素数个数（纯 CPU）。"""
    lo = int(item.get("lo", 2))
    hi = int(item.get("hi", 100000))
    cnt = 0
    for k in range(max(2, lo), hi):
        is_p = True
        d = 2
        r = int(math.isqrt(k))
        while d <= r:
            if k % d == 0:
                is_p = False
                break
            d += 1
        if is_p:
            cnt += 1
    return {"lo": lo, "hi": hi, "primes": cnt}


def w_hash(item):
    """对 seed 连续做 N 轮 SHA-256（CPU 密集、易分布式）。"""
    n = int(item.get("rounds", 100000))
    h = str(item.get("seed", "seed")).encode()
    for _ in range(n):
        h = hashlib.sha256(h).digest()
    return {"rounds": n, "digest": h.hex()[:16]}


def w_compute(item):
    """通用数值积累（iterations 控制量）；用作可调负载的基准体。"""
    it = int(item.get("iterations", 1_000_000))
    acc = 0.0
    for i in range(it):
        acc += math.sqrt((i % 1000) + 1)
    return {"iterations": it, "acc": acc}


def w_montecarlo(item):
    """蒙特卡洛估 π：撒 n 个点，返回落在单位圆内的个数（可并行求和）。"""
    n = int(item.get("samples", 1_000_000))
    seed = int(item.get("seed", 1)) & 0x7fffffff
    inside = 0
    # 线性同余自带随机，免依赖、可复现
    x = seed or 1
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7fffffff
        fx = (x / 0x7fffffff)
        x = (1103515245 * x + 12345) & 0x7fffffff
        fy = (x / 0x7fffffff)
        if fx * fx + fy * fy <= 1.0:
            inside += 1
    return {"samples": n, "inside": inside}


def model_sim_cost(item):
    """估算一次模拟推理的 compute 迭代数（token 数 × 输入宽度 × 层因子）。
    用于把'本地模型'分片折算成手机 worker 支持的 compute 任务，等价算力。"""
    width = max(16, len(str(item.get("prompt", ""))))
    tokens = int(item.get("max_tokens", 32))
    return tokens * width * 64


def model_sim_text(item):
    """只生成回复文本（不做重计算）。供'手机已用 compute 干完重活'后由老板补文本。"""
    prompt = str(item.get("prompt", ""))
    tokens = int(item.get("max_tokens", 32))
    text = "[sim:%d toks] %s" % (tokens, (prompt[:40] + "…") if len(prompt) > 40 else prompt)
    return {"prompt": prompt, "tokens": tokens, "text": text}


def w_model_sim(item):
    """
    本地模型推理【模拟后端】：在没有真实模型/依赖时，用与 token 数成正比的
    数值运算来"代表"一次前向推理的算力消耗，让整条分布式推理流水线能端到端跑通、
    并真实体现按算力比例切分的效果。换成真实后端见 projects.LocalModelProject。
    """
    acc = 0.0
    for i in range(model_sim_cost(item)):
        acc += math.sin(i * 0.001) * math.cos(i * 0.002)
    out = model_sim_text(item)
    out["_acc"] = round(acc, 3)
    return out


# 名称 → 执行体。projects/executor 通过名字找函数（也用于派给手机时的 job type 映射）。
REGISTRY = {
    "prime": w_prime,
    "hash": w_hash,
    "compute": w_compute,
    "montecarlo": w_montecarlo,
    "model_sim": w_model_sim,
}


def run_item(kind, item):
    """按 kind 执行单个 item。供本地执行引擎调用。"""
    fn = REGISTRY.get(kind)
    if fn is None:
        return {"error": "unknown workload: %s" % kind}
    return fn(item)
