# 分布式算力共享系统 · PC 协调端 / Distributed Compute-Sharing · PC Coordinator

把你的 **PC + 安卓手机** 组成一套 **你自己的** 分布式算力池，并行加速你自己的项目
（转码 / 批处理 / 编译 / 数值计算），自带算力租用计费框架 + 全维度安全阈值守门。

> **English** — A self-hosted distributed compute-sharing system: turn your **PC +
> Android phone** into your own compute pool to accelerate **your own** projects
> (transcoding / batch / compute), with a rental-billing framework and full safety
> thresholds (GREEN/YELLOW/RED). **It does NOT and cannot contribute compute to
> Claude/Anthropic for credits** — no such channel exists; this only accelerates your
> own projects on your own LAN.
>
> 📖 **每个函数的用法见 [API.md](API.md)（中英对照）/ See [API.md](API.md) for every
> function's usage (bilingual).**
> 📱 配套安卓 worker / Companion Android worker:
> https://github.com/MagicGirl2000/compute-share-android

> 配套手机端工程：`C:\Users\victo\AndroidStudioProjects\2`（安卓 worker，Kotlin + Compose）。
> 运行环境：Windows + Python 3.8（本目录 `.venv`），Flask + psutil。

---

## ⚠️ 必读现实说明（避免误解）

- 本系统是 **你自己的** PC↔手机分布式算力，**只能加速你自己的项目**。
- 它 **不能** 把算力“贡献给 Claude / Anthropic 换取 Pro 额度或优惠券”——
  那种通道在现实中**不存在**。Claude 只运行在 Anthropic 自己的服务器上，
  没有任何“贡献本地算力换额度”的官方机制。本系统与之无关。
- “算力租用”= 你把自己的设备租给**你认识的第三方**用，自行结算（或用产生的费用抵你的成本）。

---

## 一、能做什么

| 能力 | 说明 |
|---|---|
| 算力聚合 | 手机 / 其它 PC 作为 worker 连入，统一接受任务 |
| 任务分发 | 大任务切片，派给空闲 worker 并行算，结果汇总 |
| 算力租用计费 | CPU核秒 / 内存MB·时 / 磁盘MB·时 / 流量MB / GPU秒，各有单价，自动累加 |
| 安全阈值守门 | 任意设备 CPU/内存/温度/电量/磁盘/流量 超阈值 → 自动降级/暂停，保护设备 |
| 可视化看板 | 浏览器实时看设备、任务、计费 |
| 看门狗(可选) | Claude/进程超时 → 自动安全恢复（flush DNS / 重启服务 / 清内存）|

---

## 二、快速开始

```bat
:: 1. 装依赖（首次）
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 2. 启动协调端 —— ★必须用这个 bat（它先杀掉所有旧实例，再启动）
启动协调端.bat
```

启动后：
- **看板**：浏览器开 `http://127.0.0.1:9000/dashboard`
- **手机连接地址**：
  - 安卓模拟器(AVD)：`http://10.0.2.2:9000` （10.0.2.2 是 AVD 访问宿主机的固定 IP）
  - 真机(同一 WiFi)：`http://<PC局域网IP>:9000` （PC 上 `ipconfig` 查 IPv4）

> ★ 为什么必须用 `启动协调端.bat`：直接 `python coordinator.py` 多跑几次会**积累多个
> 进程抢同一个 9000 端口**，导致手机“时连时断”。bat 每次启动前先杀光旧实例。

---

## 三、文件结构

| 文件 | 作用 |
|---|---|
| `coordinator.py` | **主程序**：REST API + 看板 + 任务队列 + 计费。系统大脑。端口 9000。 |
| `safety.py` | **安全阈值模块**：psutil 读资源 + GREEN/YELLOW/RED 三档判定。 |
| `test_worker.py` | 本机测试 worker：模拟 worker 跑完整链路，验证服务端逻辑。 |
| `watchdog.py` | **看门狗**：Claude/进程超时 → 自动安全恢复（详见第七节）。 |
| `启动协调端.bat` | 一键启动（先杀旧实例，根治“多进程抢端口=时连时断”）。 |
| `requirements.txt` | 依赖：Flask + psutil。 |
| `cc_state.json` | 运行时状态（设备/任务），自动生成。 |
| `cc_billing.json` | 计费记录，自动累加（重启不丢）。 |

---

## 四、REST API（手机 worker 调用）

| 方法 路径 | 作用 |
|---|---|
| POST `/api/register` | worker 注册/心跳，上报资源 → 返回安全档位 + 阈值（手机据此动态同步阈值）|
| POST `/api/pull_job` | worker 拉任务（按档位+任务weight过滤，见第六节）|
| POST `/api/complete_job` | worker 交结果 + 上报用量 → 记账 |
| POST `/api/submit_job` | 你提交一个要加速的任务 |
| GET `/api/status` | 全局状态（设备/任务/计费/本机资源）|
| GET `/dashboard` | 可视化看板 |

### 提交任务（PowerShell 示例）
```powershell
# weight 可选 "small" | "normal" | "heavy"
$body = '{"type":"prime","payload":{"limit":500000},"weight":"normal"}'
Invoke-WebRequest "http://127.0.0.1:9000/api/submit_job" -Method POST `
  -Body $body -ContentType application/json -UseBasicParsing
```

### 批量灌任务（让手机持续有活干）
```powershell
for($i=0;$i -lt 30;$i++){
  $lim = 300000 + ($i % 6)*100000
  Invoke-WebRequest "http://127.0.0.1:9000/api/submit_job" -Method POST `
    -Body ('{"type":"prime","payload":{"limit":'+$lim+'},"weight":"small"}') `
    -ContentType application/json -UseBasicParsing | Out-Null
}
```

内置任务类型（在手机 `Worker.kt` 的 `runJob()` 实现）：
- `echo` 回显（连通性测试）
- `prime` 素数计数（CPU 密集）
- `hash` SHA-256 哈希链（CPU 密集）
- `compute` 通用数值计算
> 真实任务（转码切片 / 图片批处理）按同样模式在 `runJob()` 加 `when` 分支。

> ★ 任务粒度建议：单任务跑 **2–5 秒** 最合适——太快(秒完)UI 看着像“空闲”，
> 太重(prime limit 500万→手机要算 50秒)看着像“卡住”（其实在玩命算）。
> 手机比 PC 慢很多，limit 30–120万比较合适。

---

## 五、安全阈值（`safety.py`）

三档：**GREEN**(全速) / **YELLOW**(只接 small 任务) / **RED**(拒绝一切新任务)。

| 指标 | YELLOW | RED |
|---|---|---|
| CPU 占用% | 75 | 90 |
| 内存占用% | 80 | 92 |
| 磁盘占用% | 85 | 95 |
| 温度℃(PC) | 70 | 82 |
| 电量%(手机/笔记本) | 35 | 20 |
| 空闲内存 | — | <256MB |
| 会话流量 | — | >5000MB |

- 环境变量覆盖：`SAFE_CPU_RED=85`、`SAFE_MIN_FREE_MEM_MB=512`、`SAFE_REQUIRE_CHARGING=1` 等。
- 手机端阈值在 `Safety.kt` 同构实现（电池温度门槛更低：38/44℃）。

---

## 六、派任务的过滤逻辑（★ 含一个已修 bug）

`/api/pull_job` 按 **档位 + 每个任务自身 weight** 过滤：
- **RED** → 全拒（设备保护，只允许心跳）
- **YELLOW** → 只派 `weight=small` 的任务
- **GREEN** → 派任意任务

> ★ 已修 bug（2026-06-01）：旧版 `pull_job` 写死按 `"normal"` 判定，导致 YELLOW 档时
> **连标了 small 的任务都不派**（手机一直显示“YELLOW：只接小任务，暂无”）。
> 现已改为按任务自身 weight 过滤。

---

## 七、看门狗 `watchdog.py`（Claude 超时自动恢复）

需求：Claude/某进程 超过 90 秒无响应 → 自动做网络/系统恢复。

★ **默认只做安全恢复，不会断你的 VM 商店项目**：
1. `ipconfig /flushdns` 刷新 DNS
2. 重启本地 Flask 服务（商店服务端 + 算力协调端，若挂了）
3. 触发一次内存清理（跑 `清理内存.ps1`）

★ **360 断网急救箱的“立即修复”默认关闭**——它会重置网卡/Winsock，很可能断掉
VMware NAT(192.168.206.1) → 把 WM 商店项目搞断。只有你**明确**设 `ENABLE_360_REPAIR=1`
才会调 360（且仍优先安全恢复）。

```bat
set WD_TIMEOUT=90                 :: 无响应阈值秒（默认90=1分半）
set WD_HEARTBEAT=D:\path\hb.txt   :: 心跳文件（被监控方定期 touch；可选）
set ENABLE_360_REPAIR=0           :: 默认0关闭；1=危险，允许调360
.venv\Scripts\python.exe watchdog.py
```

---

## 八、算力租用计费单价（`coordinator.py` 的 `PRICES`，单位：元）

| 资源 | 单价 |
|---|---|
| CPU 每核·秒 | 0.00002 |
| 内存 每MB·小时 | 0.00001 |
| 磁盘 每MB·小时 | 0.000002 |
| 网络 每MB | 0.0001 |
| GPU 每秒 | 0.0005 |

记账累加进 `cc_billing.json`，看板“算力租用记账”区可看每设备用量与费用。
**减免租金用法**：别人租你设备产生的费用，可抵你自己付出的成本。

---

## 九、排错（★ 全是踩过的真坑）

- **“有时连不上 / 时连时断”** = **多个 coordinator.py 进程抢 9000 端口**。
  → 永远用 `启动协调端.bat`。手动检查只应有 1 个：
  ```powershell
  Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -like '*coordinator.py*' }
  ```
- **手机一直 RED**：看看板/手机的“原因”。常见：小内存设备空闲内存不足
  （阈值已降到 256MB）、“仅充电时贡献”开着但没充电。
- **手机一直“YELLOW 暂无”**：派的任务都是 normal，YELLOW 只接 small。
  → 灌任务时带 `"weight":"small"`。
- **手机看着像“卡住”**：多半是任务太重（prime limit 太大，手机要算几十秒）。
  → limit 降到 30–120万。看真相用 `adb logcat -s CpuWorker`。
- **模拟器连不上宿主机**：必须用 `10.0.2.2`（不是 192.168.x）。
  验证：`adb shell ping 10.0.2.2`。
- **改了 coordinator.py / safety.py** 必须重启协调端才生效。

---

## 十、已验证（2026-06-01）

安卓模拟器(API 36)作为 worker 连上 PC 协调端，**持续高速跑任务**：
- 累计完成 80+ 个任务，任务 ID 持续流动（J00073→74→75→…），手机 UI 实时显示
  “执行任务 Jxxxx (prime)”，完成数持续增长（实锤截图）。
- 结果全部正确：5万内素数=5133、50万=41538、200万=148933 等。
- 安全阈值守门有效：内存高→YELLOW降级/RED暂停，降下来→恢复GREEN。
- 计费准确累加（手机已 1493+ CPU核秒）。
- 连接稳定性已修（worker `post()` 加 3 次重试 + 拉长超时 + Connection:close）。
