# 算力分享 Worker · 安卓端 / Compute-Sharing Worker · Android

把这台安卓手机作为 **worker**，连到你 PC 上的**协调端**，贡献算力并行加速**你自己的项目**。
全程受**安全阈值**保护（电量低 / 未充电 / 过热 / 内存紧张会自动暂停）。

> **English** — Use this Android phone as a **worker** that connects to the PC
> **coordinator** and contributes compute to accelerate **your own** projects, fully
> guarded by **safety thresholds** (auto-pause on low battery / not charging /
> overheating / low memory). **It does NOT and cannot contribute compute to
> Claude/Anthropic for credits** — no such channel exists.
>
> 📖 **每个类与函数的用法见 [API.md](API.md)（中英对照）/ See [API.md](API.md) for
> every class & function's usage (bilingual).**
> 💻 配套 PC 协调端 / Companion PC coordinator:
> https://github.com/MagicGirl2000/compute-share-coordinator

> 配套 PC 协调端：`D:\电脑算力租用包含安全阈值`（Python + Flask）。
> 目标机型：三星 S25 Edge（任意 Android 5.0+ / API 21+ 均可）。
> 技术栈：Kotlin + Jetpack Compose + Material3，包名 `com.example.cpu`。

---

## ⚠️ 必读现实说明

- 本 app 只把算力贡献给 **你自己的 PC 协调端（局域网）**，加速你的项目。
- **不会也无法** 把算力贡献给 Claude / Anthropic 换取额度或优惠券——那种通道
  在现实中**不存在**。app 界面底部也写明了这一点。
- 全程受安全阈值保护：电量低 / 未充电 / 过热 / 内存紧张 会**自动暂停**贡献。

---

## 一、使用步骤

1. **PC 先启动协调端**：在 PC 上双击 `D:\电脑算力租用包含安全阈值\启动协调端.bat`。
2. 手机装本 app，打开“⚡ 算力分享 Worker”。
3. 填“PC 协调端地址”：
   - **安卓模拟器(AVD)**：`http://10.0.2.2:9000`（10.0.2.2 = AVD 访问宿主机的固定 IP）
   - **真机(同一 WiFi)**：`http://<PC局域网IP>:9000`（PC 上 `ipconfig` 查 IPv4）
4. （可选）打开“仅充电时贡献”保护电池。
5. 点 **开始贡献算力**。
6. 在 PC 浏览器开 `http://127.0.0.1:9000/dashboard`，能看到这台手机出现、任务在跑、计费在涨。

### UI 各状态含义
- `安全阈值: GREEN/YELLOW/RED` —— 当前档位 + 原因。
- `执行任务 Jxxxx (prime)` —— **正在算任务**（不是空闲）。
- `空闲，等待任务…` —— 已连上，但协调端暂无任务。
- `YELLOW：只接小任务，暂无` —— 资源吃紧，只接 small 任务，当前队列没有。
- `已完成任务: N` / `本会话流量 X MB` —— 真实累计。

---

## 二、工程结构

| 文件 | 作用 |
|---|---|
| `app/src/main/java/com/example/cpu/MainActivity.kt` | Compose UI：资源面板/档位/地址输入/开关/状态/计费 |
| `app/src/main/java/com/example/cpu/Worker.kt` | **worker 引擎**：轮询协调端、跑任务、上报用量。含连接重试 + 日志 |
| `app/src/main/java/com/example/cpu/Safety.kt` | **安全阈值 + 资源读取**（与 PC `safety.py` 同构）|
| `app/src/main/AndroidManifest.xml` | 权限：INTERNET / 网络状态 / 唤醒锁 + `usesCleartextTraffic`(允许明文 http) |

---

## 三、worker 工作循环（`Worker.kt` 的 `loop()`）

```
读本机资源(Safety.read)
  → 过安全阈值(Safety.evaluate) → 更新 UI 档位
  → register 心跳（始终上报，即使 RED；顺便同步协调端下发的阈值）
  → RED?  是 → 暂停，只心跳，不接任务
          否 → pull_job 拉任务
  → 拉到? 否 → “空闲/YELLOW暂无”，等 2.5 秒再轮询
          是 → runJob(prime/hash/compute/echo) 本地计算
             → complete_job 上报结果 + 用量(计费)
             → 完成数+1，记 logcat
```

**加新任务类型**：在 `Worker.kt` 的 `runJob()` 里加 `when` 分支
（比如真实的转码切片、图片批处理、编译单元）。

---

## 四、安全阈值（`Safety.kt`）

三档 GREEN/YELLOW/RED，与 PC 端同构。手机额外重视：

| 指标 | YELLOW | RED |
|---|---|---|
| CPU 占用% | 75 | 90 |
| 内存占用% | 80 | 92 |
| **电池温度℃** | 38 | 44 （过热保护，比 PC 低）|
| 电量% | 35 | 20 |
| 空闲内存 | — | <256MB |

- **“仅充电时贡献”开关**：开了则未充电直接 RED（保护电池）。
- 阈值会从协调端 `/api/register` 响应**动态同步**——PC 端改阈值，手机自动跟随。

---

## 五、连接稳定性（已修）

`Worker.kt` 的 `post()` 已加固，解决早期“有时连不上”：
- **3 次重试**（递增退避 1.5s / 3s）
- **拉长超时**：连接 8s、读取 60s
- **`Connection: close`**：避免连接复用导致的半死链
- **失败有 logcat**：`POST /api/xxx 第N次失败: ...`

---

## 六、排错

- **连接失败**：
  - 模拟器必须用 `10.0.2.2`（不是 192.168.x）；真机要和 PC 同一 WiFi。
  - 确认 PC 协调端在跑、且只有 1 个实例（多实例抢端口会时连时断）。
  - PC 防火墙可能拦 9000 端口，放行 `python.exe`。
- **一直 RED 不接任务**：看 app 状态栏原因。常见：电量低 / 未充电(关“仅充电”开关) /
  内存紧张(小内存设备/模拟器)。
- **一直“YELLOW 暂无”**：资源吃紧只接 small，而 PC 派的是 normal 任务。
  → PC 灌任务时带 `"weight":"small"`。
- **看着像“卡住”不动**：多半是任务太重（手机算一个要几十秒）。看真相：
  ```
  adb logcat -s CpuWorker
  ```
  能看到“worker 启动 / 领取任务 Jxxxx / 完成 Jxxxx, CPU N核秒 / 连接失败重试”。
- **改了 .kt 代码**：必须在 Android Studio 点 ▶ Run 重新编译安装才生效。

---

## 七、已验证（2026-06-01）

模拟器(API 36)作为 worker 成功连上 PC 协调端，**持续高速跑任务**：
- UI 实锤：状态显示“执行任务 J00082 (prime)”、已完成 88、流量 1.88 MB、档位 GREEN。
- 任务 ID 持续流动（J00073→74→75→…），证明不是空闲、不是卡住。
- 结果正确（5万素数=5133 等），计费准确累加（1493+ CPU核秒）。
- 安全阈值守门有效：内存高→YELLOW降级→恢复GREEN。
