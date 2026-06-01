# -*- coding: utf-8 -*-
"""
配置 (Config)
=============
算力老板的全局配置。优先级：环境变量 > boss_config.json > 默认值。
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "boss_config.json")

VERSION = "beta-0.2"   # 三端同步版本（协调端 / 安卓矿工 / Windows矿工 / 老板）

DEFAULTS = {
    # 老板自己的 Web 控制台端口
    "boss_port": 8000,
    # 下游"协调端"地址（手机 worker 接在它上面）。老板把手机的那一份任务提交到这里。
    "coordinator_url": "http://127.0.0.1:9000",
    # 是否在老板进程内启动 PC 本地执行引擎（用电脑全部关键硬件干自己那一份）
    "use_local_pc": True,
    # PC 本地引擎最多用多少进程（None=用全部物理核-1，给系统留一个）
    "pc_max_workers": None,
    # 是否尝试用 GPU（需 numpy+可选 cupy/torch；探测不到自动退回 CPU）
    "enable_gpu": True,
    # 能力基准测试每轮时长（秒）。越长越准，越短越快。
    "benchmark_seconds": 1.2,
    # 设备能力评分的缓存有效期（秒）；过期自动重测。
    "capability_ttl": 120,
    # 本地模型项目的后端： "sim"(零依赖模拟) | "llama_cpp" | "transformers"
    "model_backend": "sim",
    # 真实模型路径（llama_cpp 用 gguf 路径；transformers 用模型名/目录）
    "model_path": "",
    # 单个分片（shard）默认包含多少个 work-item
    "shard_size": 64,
    # 轮询协调端任务结果的间隔（秒）
    "poll_interval": 1.0,

    # ── beta0.2 开发任务加速 ──
    # 共享缓存根目录（wheelhouse / gradle 缓存 / 插件 都放这）
    "cache_dir": os.path.join(HERE, "cache"),
    # Gradle 共享缓存目录（构建缓存命中→重复构建快）
    "gradle_home": os.path.join(HERE, "cache", "gradle"),
    # adb 路径（多端部署/装真机用；空=从 PATH 找）
    "adb_path": "",
    # 默认安卓工程目录（构建 APK / 一键部署用；空=每次填）
    "apk_project_dir": "",
    # 工人 app 最新 APK 路径（自动更新工人用）
    "worker_apk_path": "",
}


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    # 环境变量覆盖：BOSS_PORT / BOSS_COORDINATOR_URL ...
    for k in cfg:
        env = os.environ.get("BOSS_" + k.upper())
        if env is None:
            continue
        cur = cfg[k]
        try:
            if isinstance(cur, bool):
                cfg[k] = env == "1"
            elif isinstance(cur, int) and cur is not None:
                cfg[k] = int(env)
            elif isinstance(cur, float):
                cfg[k] = float(env)
            else:
                cfg[k] = env
        except Exception:
            pass
    return cfg


def save(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False
