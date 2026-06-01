# 函数与 API 完整参考 / Full Function & API Reference

> 中英对照 · 列出每个函数的用法，方便快速读懂代码。
> Bilingual · Every function's usage, so anyone can read the code quickly.

---

## `coordinator.py` — 主程序 / Main coordinator

Flask 服务，端口 9000。系统大脑：注册设备、派任务、记账、看板。
Flask server on port 9000. The brain: register devices, dispatch jobs, billing, dashboard.

### 内部工具 / Internal helpers

| 函数 Function | 用法 Usage |
|---|---|
| `_load()` | 启动时从 `cc_state.json` 读回设备/任务状态。无参。/ Load devices & jobs from `cc_state.json` at startup. No args. |
| `_save()` | 把当前状态写回 `cc_state.json`。每次改动后调用。/ Persist current state to disk after each change. |
| `_now()` | 返回当前 Unix 时间戳(float)，用于心跳计时。/ Return current Unix timestamp (float) for heartbeat timing. |
| `_ts()` | 返回 `"YYYY-MM-DD HH:MM:SS"` 字符串，用于人类可读的时间。/ Return human-readable timestamp string. |
| `add_billing(device_id, usage)` | 给某设备累加用量并算费用。`usage` 是 dict(cpu_core_sec/mem_mb_hour/disk_mb_hour/net_mb/gpu_sec)。返回本次费用(float)，并写入 `cc_billing.json`。/ Accumulate a device's usage & compute cost; returns this call's cost and writes `cc_billing.json`. |

### REST API 端点 / REST endpoints

#### `POST /api/register` → `api_register()`
worker 注册/心跳。/ Worker registration & heartbeat.
- **请求体 Body**: `{device_id, name, kind, resources{cpu,mem,mem_free_mb,...}, caps{}, net_session_mb}`
- **返回 Returns**: `{ok, level, reasons, thresholds}` —— level 是 GREEN/YELLOW/RED。
- **示例 Example**:
  ```bash
  curl -X POST http://127.0.0.1:9000/api/register -H "Content-Type: application/json" \
    -d '{"device_id":"phone1","name":"S25","kind":"phone","resources":{"cpu":10,"mem":40,"mem_free_mb":4000,"cores":8,"battery":80,"charging":true}}'
  ```

#### `POST /api/pull_job` → `api_pull_job()`
worker 拉一个任务。/ Worker pulls one job.
- **请求体**: 同 register(带 resources)。/ Same as register (with resources).
- **过滤逻辑 Filter**: RED→不给；YELLOW→只给 `weight=small`；GREEN→任意。
- **返回**: `{ok, job{id,type,payload,...}|null, level, paused}` —— job 为 null 表示暂无可派。

#### `POST /api/complete_job` → `api_complete_job()`
worker 交结果 + 上报用量 → 记账。/ Worker submits result + usage → billed.
- **请求体**: `{device_id, job_id, result{}, usage{cpu_core_sec,mem_mb_hour,net_mb,gpu_sec}}`
- **返回**: `{ok, billed}` —— billed 是本任务计费(元)。

#### `POST /api/submit_job` → `api_submit_job()`
你提交一个要加速的任务。/ You submit a job to be accelerated.
- **请求体**: `{type, payload{}, weight}` —— type ∈ echo/prime/hash/compute；weight ∈ small/normal/heavy。
- **返回**: `{ok, job_id}` —— 如 `"J00007"`。
- **示例**:
  ```bash
  curl -X POST http://127.0.0.1:9000/api/submit_job -H "Content-Type: application/json" \
    -d '{"type":"prime","payload":{"limit":500000},"weight":"normal"}'
  ```

#### `GET /api/status` → `api_status()`
全局状态(看板用)。/ Global status (used by dashboard).
- **返回**: `{devices{}, jobs[], billing{}, local{}, local_level, thresholds, prices}`

#### `GET /` `GET /dashboard` → `dashboard()`
返回可视化看板 HTML。浏览器打开。/ Returns the dashboard HTML. Open in a browser.

---

## `safety.py` — 安全阈值 / Safety thresholds

| 函数 Function | 用法 Usage |
|---|---|
| `load_thresholds(overrides=None)` | 加载阈值字典。`overrides` 可覆盖默认值；环境变量 `SAFE_CPU_RED=85` 也能覆盖。返回完整阈值 dict。/ Load threshold dict; `overrides` and env vars `SAFE_*` can override. |
| `read_resources()` | 用 psutil 读本机资源，返回 `{cpu,mem,mem_free_mb,disk,temp,battery,charging,cores,...}`。手机端在 `Safety.kt` 同构。/ Read this machine's resources via psutil. |
| `evaluate(resources, thresholds, net_session_mb=0.0)` | 判定档位。返回 `(level, reasons)` —— level∈{GREEN,YELLOW,RED}，reasons 是触发原因列表。/ Evaluate level; returns (level, reasons list). |
| `can_accept_job(resources, thresholds, net_session_mb=0.0, job_weight="normal")` | 是否可接某权重任务。返回 `(ok:bool, level, reasons)`。GREEN 接全部；YELLOW 只接 small；RED 全拒。/ Whether a job of given weight can be accepted. |

**直接运行 `python safety.py`** 会打印本机资源 + 当前档位，用于快速自检。
Running `python safety.py` prints resources + current level for a quick self-check.

---

## `watchdog.py` — 看门狗 / Watchdog

Claude/进程超时(默认90秒无响应)→ 自动安全恢复。
On Claude/process timeout (default 90s idle) → automatic safe recovery.

| 函数 Function | 用法 Usage |
|---|---|
| `log(msg)` | 写一行带时间戳的日志到控制台 + `_watchdog.log`。/ Timestamped log line. |
| `heartbeat_age()` | 返回心跳文件距今多少秒未更新(秒)。被监控方定期 touch 该文件。/ Seconds since heartbeat file last updated. |
| `ping_ok(host)` | ping 主机，通返回 True。/ Ping host, True if reachable. |
| `port_listening(port)` | 检测本机端口是否在监听(服务是否活着)。/ Check if a local port is listening. |
| `safe_recover(reason)` | ★安全恢复：flushdns + 重启 Flask 服务 + 清内存。**不碰 VMware/网卡**。/ Safe recovery: flush DNS + restart Flask + clean memory. Never touches VMware/NIC. |
| `repair_360()` | 调 360 断网急救箱修复。**默认禁用**(会重置网卡断 VM)，需 `ENABLE_360_REPAIR=1`。/ Call 360 network repair. Disabled by default (resets NIC, breaks VM). |
| `main()` | 主循环：定期检查心跳/网络，超时则 `safe_recover()`。/ Main loop: periodically check, recover on timeout. |

**环境变量 / Env vars**: `WD_TIMEOUT`(默认90秒) `WD_HEARTBEAT`(心跳文件路径) `ENABLE_360_REPAIR`(默认0)。

---

## `test_worker.py` — 本机测试 worker / Local test worker

直接 `python test_worker.py` 运行：模拟一个 worker，把队列里的任务全部拉下来跑完，
验证 register→pull→run→complete→billing 全链路。无函数参数，开箱即用。
Run `python test_worker.py`: simulates a worker that pulls & runs all queued jobs,
verifying the full register→pull→run→complete→billing chain.
