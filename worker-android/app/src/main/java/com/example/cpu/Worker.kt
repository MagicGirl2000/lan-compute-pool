package com.example.cpu

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import kotlin.math.sqrt

private const val TAG = "CpuWorker"   // logcat 过滤用：adb logcat -s CpuWorker

/**
 * 算力分享 worker 引擎。
 * 循环：读资源 → 过安全阈值 → register 心跳 → pull_job → 跑任务 → complete_job(上报用量)。
 * 全程受 Safety 守门：RED 不接活、只心跳；YELLOW 只接 small。
 *
 * 内置任务类型（演示 + 真能加速你项目的活）：
 *   - "echo"    回显（连通性测试）
 *   - "hash"    批量 SHA-256（CPU 密集，适合分布式）
 *   - "prime"   素数计数（纯 CPU 算力 benchmark）
 *   - "compute" 通用数值计算（payload.iterations 控制量）
 * 你项目的真实任务(转码切片/批处理)按同样模式加 case 即可。
 */
class Worker(
    private val ctx: Context,
    @Volatile var serverUrl: String,
    private val deviceId: String,
    private val deviceName: String,
) {
    @Volatile var running = false
    @Volatile var thresholds = Thresholds()
    @Volatile var lastLevel: Level = Level.GREEN
    @Volatile var lastReasons: List<String> = listOf("未启动")
    @Volatile var lastRes: Resources = Resources()
    @Volatile var jobsDone = 0
    @Volatile var netSessionMb = 0.0
    @Volatile var statusLine = "空闲"
    @Volatile var requireCharging = false

    var onUpdate: (() -> Unit)? = null

    private var thread: Thread? = null

    fun start() {
        if (running) return
        running = true
        Log.i(TAG, "worker 启动，目标协调端=$serverUrl")
        thread = Thread { loop() }.apply { isDaemon = true; start() }
    }

    fun stop() {
        running = false
        statusLine = "已停止"
        onUpdate?.invoke()
    }

    private fun loop() {
        while (running) {
            try {
                val res = Safety.read(ctx)
                lastRes = res
                val t = thresholds.copy(requireCharging = requireCharging)
                val (lvl, reasons) = Safety.evaluate(res, t)
                lastLevel = lvl; lastReasons = reasons

                // 心跳注册（始终上报，即使 RED——让协调端知道我在线但不可用）
                register(res, lvl)

                if (lvl == Level.RED) {
                    statusLine = "安全阈值 RED：暂停接任务（${reasons.joinToString("；")}）"
                    onUpdate?.invoke()
                    sleep(3000); continue
                }

                // 拉任务
                val job = pullJob(res)
                if (job == null) {
                    statusLine = if (lvl == Level.YELLOW) "YELLOW：只接小任务，暂无" else "空闲，等待任务…"
                    onUpdate?.invoke()
                    sleep(2500); continue
                }

                val jid = job.optString("id"); val jtype = job.optString("type")
                Log.i(TAG, "领取任务 $jid ($jtype)")
                statusLine = "执行任务 $jid ($jtype)"
                onUpdate?.invoke()
                val t0 = System.nanoTime()
                val result = runJob(job)
                val cpuSec = (System.nanoTime() - t0) / 1e9 * res.cores

                completeJob(jid, result, cpuSec, res)
                jobsDone++
                Log.i(TAG, "完成 $jid，CPU ${"%.1f".format(cpuSec)}核秒，累计完成 $jobsDone")
                onUpdate?.invoke()
            } catch (e: Exception) {
                Log.w(TAG, "loop 异常: ${e.message}")
                statusLine = "错误：${e.message}（重试中）"
                onUpdate?.invoke()
                sleep(3000)
            }
        }
    }

    // ── 任务执行 ────────────────────────────────────────────────────────────
    private fun runJob(job: JSONObject): JSONObject {
        val type = job.optString("type")
        val p = job.optJSONObject("payload") ?: JSONObject()
        val out = JSONObject()
        when (type) {
            "echo" -> out.put("echo", p.optString("msg", ""))
            "hash" -> {
                val n = p.optInt("count", 100000)
                val seed = p.optString("seed", "seed")
                val md = MessageDigest.getInstance("SHA-256")
                var h = seed.toByteArray()
                for (i in 0 until n) h = md.digest(h)
                out.put("hash", h.joinToString("") { "%02x".format(it) }.take(16))
                out.put("rounds", n)
            }
            "prime" -> {
                val limit = p.optInt("limit", 200000)
                var cnt = 0
                for (k in 2..limit) {
                    var isP = true
                    var d = 2
                    while (d <= sqrt(k.toDouble()).toInt()) { if (k % d == 0) { isP = false; break }; d++ }
                    if (isP) cnt++
                }
                out.put("primes", cnt); out.put("limit", limit)
            }
            "compute" -> {
                val iters = p.optInt("iterations", 1_000_000)
                var acc = 0.0
                for (i in 0 until iters) acc += sqrt((i % 1000 + 1).toDouble())
                out.put("acc", acc); out.put("iterations", iters)
            }
            else -> out.put("warn", "unknown job type: $type")
        }
        return out
    }

    // ── HTTP ────────────────────────────────────────────────────────────────
    private fun baseBody(res: Resources): JSONObject = JSONObject().apply {
        put("device_id", deviceId)
        put("name", deviceName)
        put("kind", "phone")
        put("net_session_mb", netSessionMb)
        put("resources", JSONObject().apply {
            put("cpu", res.cpu); put("mem", res.mem); put("mem_free_mb", res.memFreeMb)
            put("mem_total_mb", res.memTotalMb); put("cores", res.cores)
            put("battery", res.battery); put("charging", res.charging)
            res.temp?.let { put("temp", it) }
        })
        put("caps", JSONObject().apply { put("cpu", true); put("hash", true) })
    }

    private fun register(res: Resources, lvl: Level) {
        val resp = post("/api/register", baseBody(res)) ?: return
        resp.optJSONObject("thresholds")?.let { th ->
            thresholds = Thresholds(
                cpuYellow = th.optDouble("cpu_yellow", 75.0), cpuRed = th.optDouble("cpu_red", 90.0),
                memYellow = th.optDouble("mem_yellow", 80.0), memRed = th.optDouble("mem_red", 92.0),
                tempYellow = thresholds.tempYellow, tempRed = thresholds.tempRed,
                batteryRed = th.optDouble("battery_red", 20.0).toInt(),
                batteryYellow = th.optDouble("battery_yellow", 35.0).toInt(),
                requireCharging = requireCharging,
                minFreeMemMb = th.optDouble("min_free_mem_mb", 256.0),
            )
        }
    }

    private fun pullJob(res: Resources): JSONObject? {
        val resp = post("/api/pull_job", baseBody(res)) ?: return null
        if (resp.optBoolean("paused", false)) return null
        return resp.optJSONObject("job")
    }

    private fun completeJob(jobId: String, result: JSONObject, cpuSec: Double, res: Resources) {
        val body = JSONObject().apply {
            put("device_id", deviceId)
            put("job_id", jobId)
            put("result", result)
            put("usage", JSONObject().apply {
                put("cpu_core_sec", cpuSec)
                put("mem_mb_hour", res.memTotalMb * 0.1 / 3600.0) // 估算
                put("net_mb", 0.05)
                put("gpu_sec", 0.0)
            })
        }
        post("/api/complete_job", body)
    }

    /** 带重试的 POST：连不上自动重试最多 3 次（递增退避），解决"有时连不上"。 */
    private fun post(path: String, body: JSONObject): JSONObject? {
        var lastErr: String? = null
        for (attempt in 1..3) {
            try {
                val url = URL(serverUrl.trimEnd('/') + path)
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.connectTimeout = 8000      // 拉长到 8s，AVD↔宿主机链路偶尔慢
                conn.readTimeout = 60000        // 任务结果可能大，给足
                conn.useCaches = false
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("Connection", "close")  // 避免连接复用导致的半死链
                val payload = body.toString()
                OutputStreamWriter(conn.outputStream, Charsets.UTF_8).use { it.write(payload) }
                netSessionMb += payload.toByteArray().size / 1048576.0
                val code = conn.responseCode
                val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                    ?.bufferedReader()?.use { it.readText() } ?: "{}"
                netSessionMb += text.toByteArray().size / 1048576.0
                conn.disconnect()
                return JSONObject(text)
            } catch (e: Exception) {
                lastErr = e.message
                Log.w(TAG, "POST $path 第${attempt}次失败: ${e.message}")
                if (attempt < 3) sleep(attempt * 1500L)  // 1.5s, 3s 退避
            }
        }
        statusLine = "连接失败(重试3次)：$lastErr"
        return null
    }

    private fun sleep(ms: Long) { try { Thread.sleep(ms) } catch (_: Exception) {} }
}
