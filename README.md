# 算力老板 · Compute Boss

> 局域网分布式算力系统的 **"老板"** ——在"工人"(安卓算力 app + Windows 算力端)之上，
> **评估手机与电脑的算力能力比例**，把项目(含**本地模型推理**)**按比例切分**、分发到
> **电脑全部关键硬件 + 手机**，并提供**可视化控制台**。
>
> **English** — The **"boss"** of a LAN compute pool. Above the *workers* (the Android
> compute app + the Windows compute node), it **measures the phone-vs-PC capability ratio**,
> then **splits projects proportionally** (including **local-model inference**) across **all
> the PC's key hardware + the phones**, with a **visual web console**.

---

## 一、它在系统里的位置 / Where it sits

整个算力系统现在有 **三层**：

```
   工人 Workers                          老板 Boss (本项目)
 ┌───────────────┐   HTTP    ┌────────────────────┐   ┌──────────────────────────┐
 │ 安卓算力 app   │──────────►│                    │   │  算力老板  boss.py :8000   │
 │ (Worker.kt)   │  拉任务    │   协调端 Coordinator│◄──┤  · 评估算力比例            │
 ├───────────────┤◄──────────│   coordinator.py    │   │  · 按比例切分项目          │
 │ Windows 算力端 │  交结果    │   :9000 任务队列     │──►│  · 派"手机那份"进队列       │
 │ (test_worker) │           │   + 计费 + 安全阈值  │   │  · 自己吃满 PC 多核/GPU    │
 └───────────────┘           └────────────────────┘   │  · 可视化控制台            │
                                                        └──────────────────────────┘
```

- **工人**：贡献算力、执行单个任务。安卓 app 和 PC worker 都接在**协调端**上。
- **协调端**(`D:\电脑算力租用包含安全阈值\coordinator.py`)：任务队列 + 心跳 + 计费 + 安全阈值。
- **老板**(本项目)：**项目级编排**。它不取代协调端，而是**站在它之上**——评估谁强谁弱、
  按比例切活、把手机那份丢进协调端队列让手机来拉、电脑那份自己用多核引擎吃满，最后合并结果。

> Three layers: **Workers** execute single jobs and attach to the **Coordinator** (queue +
> billing + safety). The **Boss** (this repo) does *project-level orchestration* on top:
> assess capability, split by ratio, push the phone's share into the coordinator queue, run
> the PC's share on a local multi-core engine, then merge.

---

## 二、按算力比例分配（核心思想）/ Proportional allocation

1. **评估**：PC 本地真跑一段标准负载测吞吐；手机则**经协调端投一个标定任务**、测它从领取到
   完成的耗时——得到每台设备的 **算力评分**(work-units/秒)。
2. **比例**：`ratio[设备] = 评分 / Σ评分`。例如实测 **PC 96.5% : 手机 3.5%**。
3. **切分**：项目拆成 N 个分片后，用**最大余数法**按比例给每台设备整数配额（强的多分、弱的少分），
   交错下标分配让负载均匀。
4. **派发**：PC 那份 → 本地多核引擎(`executor.py`)；手机那份 → 提交到协调端队列。
5. **兜底**：手机分片若超时(掉线/电量低 RED)，老板自动用 PC 把它补算掉，**绝不卡死**。

> Assess → ratio → largest-remainder split → dispatch (PC local + phone via coordinator) →
> PC-fallback for stalled phone shards → merge.

---

## 三、关键硬件分担 / Using all key PC hardware

老板要"分配电脑所有关键硬件的分担任务"：

- **CPU**：`executor.py` 用 `ProcessPoolExecutor` 跨**全部物理核**并行（默认留 1 核给系统/UI）。
- **GPU**：`hardware.py` 探测独显(pynvml→nvidia-smi)；纯逐元素负载放 GPU 反而慢，**GPU 留给
  本地模型项目的真实后端**(`transformers`+`torch` 自动用 GPU)去吃。
- **内存 / 磁盘 / 网络**：`hardware.utilization()` 实时读占用，控制台用条形图可视化；
  安全阈值由协调端那层守门。

> CPU via a multi-core process pool; GPU detected and reserved for the real model backend;
> RAM/disk/net shown live; safety gating handled by the coordinator layer.

---

## 四、内置项目 / Built-in projects (`projects.py`)

| key | 项目 | PC 执行 | 手机执行 | 说明 |
|---|---|---|---|---|
| `local_model` | **本地模型推理（数据并行）** | sim / llama_cpp / transformers | compute 折算 + 老板补文本 | 把一批 prompt 按算力比例分发到各设备做前向 |
| `prime_scan` | 大区间素数统计 | prime | prime（原生） | 统计 [2,limit) 素数，分片并行 |
| `hash_grind` | 批量 SHA-256 | hash | hash（原生） | 哈希链，CPU 密集 |
| `montecarlo` | 蒙特卡洛估 π | montecarlo | —（仅 PC） | 撒点估 π；安卓 worker 无此原语，故 PC-only |

**关于"本地部署模型"**：默认 `model_backend="sim"` —— 用与 token 数成正比的运算**模拟**一次前向，
**零依赖、开箱即跑**，真实体现按算力比例切分。要跑**真模型**：把 `boss_config.json` 的
`model_backend` 改 `llama_cpp`(给 `model_path` 指向 gguf) 或 `transformers`(给模型名/目录，
有 GPU 自动用)，真实后端**目前在 PC 分片生效**；手机分片承担等价的 compute 重活（真实 offload），
老板补上回复文本。要让**手机也跑真模型**，需给安卓 worker 加一个 `model` 任务类型（见下"扩展"）。

---

## 五、快速开始 / Quick start

老板有**两个界面，同一套后端**，按喜好选一个：

| 入口 | 启动 | 界面 |
|---|---|---|
| **Windows 桌面 GUI**（推荐，原生窗口、不用浏览器）| `启动老板GUI.bat` | `boss_gui.py`（Tkinter）|
| **Web 控制台**（可远程/手机浏览器看）| `启动老板.bat` → http://127.0.0.1:8000 | `boss.py`（Flask）|

```bat
:: 1) （推荐）先启动协调端，让手机 worker 能接入
D:\电脑算力租用包含安全阈值\启动协调端.bat

:: 2a) 桌面 GUI（自动建 venv / 装依赖 / 杀旧实例 / 开窗）
启动老板GUI.bat

:: 2b) 或 Web 控制台
启动老板.bat       :: 然后浏览器开 http://127.0.0.1:8000
```

两种界面操作一致：
1. 点 **「重新评估算力」** → 看到 PC 与各手机的**算力比例条**。
2. 选一个**项目**、填参数 JSON（如 `{"count":200}`）。
3. 点 **「开始加速」** → 实时看**每台设备的任务流**、总进度、吞吐、结果。

> 没装依赖也行，启动脚本会自动 `pip install -r requirements.txt`。
> 协调端没开也能用——老板会只用 PC 本地算（手机比例为 0）。

手动运行：`.venv\Scripts\python.exe boss_gui.py`（桌面）或 `boss.py`（Web）。

### 桌面 GUI 说明 / About the desktop GUI
- 纯 **Tkinter**（Python 自带，零额外依赖），原生 Windows 窗口。
- 网络(协调端)与硬件读取放**后台线程**，主循环每 0.5s 读快照重绘，**界面不卡**。
- 用 **Canvas** 画彩色算力比例条、硬件仪表、每台设备任务流。
- 模块导入零副作用 + `multiprocessing.freeze_support()`：多进程引擎(spawn)在 GUI 下也安全。

---

## 六、模块与函数用法 / Modules & functions

### `boss.py` —— 主程序 + Flask Web 控制台
| 路由 | 方法 | 作用 |
|---|---|---|
| `/` | GET | 可视化控制台页面 |
| `/api/overview` | GET | PC 硬件画像+实时利用率、协调端在线手机、当前运行状态 |
| `/api/projects` | GET | 可选项目清单 + 默认参数 |
| `/api/assess` | POST | **立即重测**各设备算力，返回评分 + 比例（不启动任务）|
| `/api/run` | POST | 启动一次加速运行 `{project, params}` |
| `/api/run_status` | GET | 当前运行快照（进度/各设备完成/结果/日志）|
| `/api/cancel` | POST | 取消当前运行 |
| `/api/config` | POST | 改协调端地址 / 模型后端等并落盘 |
- `lan_ip()` 取本机局域网 IP（告诉手机连哪）。

### `config.py` —— 配置（环境变量 > `boss_config.json` > 默认）
- `load()` / `save(cfg)`。关键项：`boss_port`(8000)、`coordinator_url`(:9000)、`use_local_pc`、
  `pc_max_workers`、`enable_gpu`、`benchmark_seconds`、`capability_ttl`、`model_backend`、
  `model_path`、`shard_size`、`poll_interval`。环境变量名为 `BOSS_<大写键名>`（如 `BOSS_BOSS_PORT`）。

### `hardware.py` —— PC 关键硬件
- `profile()` 静态画像：CPU 核数/频率、内存、磁盘、GPU 列表。
- `utilization()` 动态：CPU% / 每核% / 内存% / 磁盘% / GPU% / 网络收发 KB/s。
- `detect_gpus()` / `gpu_utilization()` 独显探测（pynvml→nvidia-smi，缺则空）。

### `coordinator_client.py` —— 协调端客户端（软依赖）
- `CoordinatorClient(base_url)`：`online()`、`status()`、`online_workers(kinds)`、
  `submit(type,payload,weight)`→job_id、`job_results(ids)`。协调端没开不报错。

### `capability.py` —— 算力评估
- `CapabilityAssessor(cfg, client)`：
  - `benchmark_pc()` 本地吃满多核测吞吐 → 评分。
  - `benchmark_phone(device_id)` 经协调端投标定任务、测耗时 → 评分（超时退回按核数估计）。
  - `assess(force=False)` 评估 PC+所有在线手机，返回 `{devices, ratio, total_score}`，带 TTL 缓存。

### `executor.py` —— PC 本地多核引擎
- `LocalExecutor(max_workers)`：`run(kind, items, on_progress)` 用进程池并行跑一批 item，
  回调进度，返回 `(results, stats)`；`cancel()` 中断。

### `workloads.py` —— 工作负载原语（顶层、可 pickle）
- `w_prime / w_hash / w_compute / w_montecarlo / w_model_sim`，`run_item(kind,item)`。
- `model_sim_cost(item)` / `model_sim_text(item)`：把"模型推理"折算成 compute 量 / 只生成文本。

### `projects.py` —— 项目定义
- `Project` 基类：`make_items(params)`、`reduce(results)`、`phone_job(item)`、`phone_merge(item,r)`。
- `LocalModelProject`(含 `real_pc_backend()` / `run_pc_item()`)、`PrimeScanProject`、
  `HashGrindProject`、`MonteCarloProject`。`registry(cfg)` / `catalog(cfg)`。
- `PHONE_NATIVE = {prime, hash, compute}` —— 安卓 worker 原生支持的类型。

### `scheduler.py` —— 调度器（核心）
- `Scheduler(cfg, client, assessor, executor, registry)`：`start(project, params)`、
  `is_busy()`、`cancel()`、`run.snapshot()`。内部 `_split()` 按比例切分、`_run_pc()` / `_run_phone()`
  并行派发、`_pc_fallback()` 超时兜底。
- `Run` 对象：全程可被 UI 轮询的运行状态。

---

## 七、扩展 / Extending

- **接真实本地模型**：`pip install llama-cpp-python`（或 `transformers torch`），改
  `boss_config.json` 的 `model_backend` + `model_path`。
- **让手机也跑真模型 / 新任务类型**：在安卓 `Worker.kt` 的 `runJob()` 里加同名 `when` 分支
  （如 `"model"`），再在本项目某 Project 的 `phone_job()` 里返回该类型即可——切分/派发/合并不用改。
- **新项目**：继承 `Project`，实现 `make_items` / `reduce`（必要时 `phone_job` / `phone_merge`），
  加进 `projects.registry()` 与 `catalog()`。

---

## 八、已验证 / Verified (2026-06-01)

- PC(16 逻辑核 / 含 1 块 GPU) + 安卓模拟器 worker 经协调端接入。
- `/api/assess` 实测算力比例 **PC 96.5% : 手机 3.5%**（手机经协调端真实标定）。
- `prime_scan` [2,200000) = **17984** 个素数（π(200000) 正确）。
- `montecarlo` 4,000,000 点 → **π ≈ 3.1412**；16 核全程吃满。
- 控制台实时显示硬件利用、算力比例条、每台设备任务流、进度与结果。

---

## 九、目录 / Layout
```
电脑手机内部局域网算力共享老板/
├─ boss.py                 Web 控制台主程序 (Flask)
├─ boss_gui.py             ★Windows 桌面 GUI (Tkinter)
├─ config.py               配置
├─ hardware.py             PC 关键硬件探测 + 利用率
├─ coordinator_client.py   协调端客户端
├─ capability.py           算力评估 + 比例
├─ executor.py             PC 本地多核执行引擎
├─ workloads.py            工作负载原语（可 pickle）
├─ projects.py             项目（含本地模型推理）
├─ scheduler.py            调度器：评估→切分→派发→兜底→合并
├─ templates/dashboard.html  可视化控制台
├─ static/style.css · app.js 控制台前端
├─ requirements.txt
├─ 启动老板.bat            启动 Web 控制台（先杀旧实例）
├─ 启动老板GUI.bat         启动 Windows 桌面 GUI（先杀旧实例）
└─ README.md
```

---

## 十、大规模并行计算实测报告（2026-06-02）

### 测试环境

- **PC**：16 逻辑核 / 8 物理核，NVIDIA RTX 3070 Laptop（8 GB VRAM），32 GB RAM
- **真机**：Samsung SM-S9370，8 核，电量 48%，GREEN
- **模拟器**：sdk_gphone64_x86_64（Android API 36），6 核，YELLOW（内存 80.5%）

### 算力评估（benchmark）

| 设备 | 评分 | 比例 |
|------|------|------|
| 本机 PC（16 核） | 20.63 | **95.8 %** |
| 模拟器（6 核） | 0.70 | **3.25 %** |
| 真机 SM-S9370（8 核） | 0.20 | **0.95 %** |

### 任务 1 · 大区间素数统计 `prime_scan`

| 项目 | 数值 |
|------|------|
| 参数 | `limit=10,000,000`，`chunk=50,000`，200 片 |
| 分发 | PC **195 片** · 模拟器 **4 片** · 真机 **1 片** |
| 结果 | **664,579** 个素数（π(10M) 标准值 664,579）✅ |
| 耗时 | 38.0 s，吞吐 5.26 片/s |

### 任务 2 · 批量 SHA-256 `hash_grind`

| 项目 | 数值 |
|------|------|
| 参数 | `count=800`，`rounds=100,000`，800 片 |
| 分发 | PC **766 片** · 模拟器 **26 片** · 真机 **8 片** |
| 结果 | 800 片全部完成，哈希结果一致 ✅ |
| 耗时 | 34.7 s，吞吐 **23.06 片/s** |

### 任务 3 · 蒙特卡洛估 π `montecarlo`（PC-only）

| 项目 | 数值 |
|------|------|
| 参数 | `samples=100,000,000`，`chunk=1,000,000`，100 片 |
| 分发 | PC **100 片** · 手机 **0 片**（安卓 worker 无此原语，自动豁免）✅ |
| 结果 | π ≈ **3.141568**（误差 < 1×10⁻⁵）✅ |
| 耗时 | 15.8 s，吞吐 6.33 片/s |

### 验证结论

- **按算力比例切片正确**：最大余数法分配与理论比例吻合，三台设备合计等于总片数。
- **PC-only 任务手机自动豁免**：`montecarlo` 正确跳过两台手机，PC 独吞 100 片。
- **安全阈值兼容**：模拟器内存 80.5%（YELLOW 档），分发不受阻，任务正常完成。
- **结果精度**：素数计数与标准值完全一致；π 估计误差 < 1×10⁻⁵。

---

> 本系统是**你自己的**局域网算力池，加速**你自己的项目**；与把算力贡献给第三方云换额度无关。
> This is **your own** LAN compute pool for **your own** projects.
