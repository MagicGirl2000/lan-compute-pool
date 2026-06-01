# 函数完整参考 / Full Function Reference

> 中英对照 · 列出每个类与函数的用法。
> Bilingual · Usage of every class & function.

---

## `Worker.kt` — worker 引擎 / Worker engine

核心类，轮询 PC 协调端、跑任务、上报用量。
Core class: polls the PC coordinator, runs jobs, reports usage.

### 构造 / Constructor
```kotlin
Worker(ctx: Context, serverUrl: String, deviceId: String, deviceName: String)
```
`serverUrl` 如 `http://10.0.2.2:9000`(模拟器) 或 `http://192.168.1.x:9000`(真机)。

### 公开方法 / Public methods
| 方法 Method | 用法 Usage |
|---|---|
| `start()` | 启动 worker 后台线程，开始轮询。点“开始贡献算力”时调用。/ Start the background polling thread. |
| `stop()` | 停止 worker。点“停止贡献算力”时调用。/ Stop the worker. |

### 可观察字段 / Observable fields（UI 读取 / read by UI）
`running`(是否运行) `lastLevel`(GREEN/YELLOW/RED) `lastReasons`(档位原因) `lastRes`(资源快照)
`jobsDone`(完成数) `netSessionMb`(会话流量) `statusLine`(状态文字) `requireCharging`(仅充电开关)
`serverUrl`(协调端地址，可运行时改) `onUpdate`(UI 刷新回调)。

### 内部方法 / Internal methods
| 方法 Method | 用法 Usage |
|---|---|
| `loop()` | 主循环：读资源→过阈值→register→(RED暂停 / 否则)pull_job→runJob→completeJob。/ Main loop. |
| `runJob(job): JSONObject` | 执行任务。内置 echo/prime(素数)/hash(SHA256链)/compute(数值)。**加新任务在这里加 when 分支**。/ Execute a job; add new task types here. |
| `baseBody(res): JSONObject` | 构造 register/pull 的公共请求体(device_id+resources+caps)。/ Build common request body. |
| `register(res, lvl)` | POST /api/register 心跳，并同步协调端下发的阈值。/ Heartbeat + sync thresholds. |
| `pullJob(res): JSONObject?` | POST /api/pull_job 拉一个任务，null=暂无。/ Pull one job. |
| `completeJob(jobId, result, cpuSec, res)` | POST /api/complete_job 交结果 + 上报用量。/ Submit result + usage. |
| `post(path, body): JSONObject?` | 带 **3 次重试 + 退避** 的 HTTP POST(解决“有时连不上”)。/ HTTP POST with 3 retries + backoff. |
| `sleep(ms)` | 线程休眠封装。/ Thread sleep wrapper. |

---

## `Safety.kt` — 安全阈值 / Safety thresholds

### 数据类 / Data classes
| 类 Class | 字段 Fields |
|---|---|
| `Resources` | `cpu, mem, memFreeMb, memTotalMb, cores, battery, charging, temp` —— 手机资源快照。/ Phone resource snapshot. |
| `Thresholds` | `cpuYellow/Red, memYellow/Red, tempYellow/Red(38/44℃), batteryRed/Yellow, requireCharging, minFreeMemMb` —— 阈值，可被协调端动态覆盖。/ Thresholds, overridable by coordinator. |
| `Level`(enum) | `GREEN / YELLOW / RED` 三档。/ Three levels. |

### `object Safety` 方法
| 方法 Method | 用法 Usage |
|---|---|
| `read(ctx): Resources` | 读手机实时资源：内存(ActivityManager)、电量/温度/充电(BatteryManager)、CPU(/proc/stat 差分)。/ Read live phone resources. |
| `evaluate(r, t): Pair<Level, List<String>>` | 判定档位 → (档位, 原因列表)。/ Evaluate level → (level, reasons). |
| `canAccept(r, t, weight): Boolean` | 是否可接某权重任务。RED 拒；YELLOW 只接 small；GREEN 全接。/ Whether a job of given weight is acceptable. |
| `readCpuPercent()` | /proc/stat 两次差分算 CPU 占用率。/ CPU % via /proc/stat diff. |

---

## `MainActivity.kt` — 界面 / UI（Jetpack Compose）

| 函数 Function | 用法 Usage |
|---|---|
| `MainActivity.onCreate()` | 初始化 Worker(默认地址 10.0.2.2:9000)，setContent 加载 Compose 界面。/ Init Worker, load Compose UI. |
| `MainActivity.onDestroy()` | 退出时 `worker.stop()`。/ Stop worker on exit. |
| `WorkerScreen(worker, deviceName)` | 主界面 Composable：标题/档位/资源面板/地址输入框/仅充电开关/状态/开始停止按钮/说明。/ Main screen composable. |
| `MetricBar(label, value)` | 资源进度条 Composable(CPU/内存)，按值变色(绿/黄/红)。/ A resource progress bar, color by value. |

---

## 典型调用流程 / Typical flow

```
用户点“开始” → worker.start()
  → loop(): Safety.read(ctx) → Safety.evaluate()
  → register()  [POST /api/register, 同步阈值]
  → 若 RED: 暂停; 否则 pullJob() [POST /api/pull_job]
  → runJob(job)  [本地算 prime/hash/compute]
  → completeJob() [POST /api/complete_job, 计费]
  → jobsDone++, onUpdate() 刷新 UI
```

User taps "Start" → `worker.start()` → loop reads resources, evaluates safety,
registers (syncs thresholds), pulls a job (unless RED), runs it locally, reports
result+usage for billing, updates the UI. Repeat.
