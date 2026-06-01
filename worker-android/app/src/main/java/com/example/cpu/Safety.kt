package com.example.cpu

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import java.io.RandomAccessFile

/**
 * 手机端安全阈值 + 资源读取（与 PC safety.py 同构）。
 *
 * 手机贡献算力前必须过安全阈值：电量低 / 未充电 / 高温 / 内存紧张 → 拒绝任务。
 * 这是保护你手机的"刹车"——算力租用绝不能把手机跑到过热、耗光电、卡死。
 */
data class Resources(
    var cpu: Double = 0.0,        // CPU 占用率 %
    var mem: Double = 0.0,        // 内存占用率 %
    var memFreeMb: Double = 0.0,
    var memTotalMb: Double = 0.0,
    var cores: Int = 1,
    var battery: Int = 100,       // 电量 %
    var charging: Boolean = false,
    var temp: Double? = null,     // 电池温度 ℃
)

data class Thresholds(
    val cpuYellow: Double = 75.0, val cpuRed: Double = 90.0,
    val memYellow: Double = 80.0, val memRed: Double = 92.0,
    val tempYellow: Double = 38.0, val tempRed: Double = 44.0,  // 手机电池温度，比PC低
    val batteryRed: Int = 20, val batteryYellow: Int = 35,
    val requireCharging: Boolean = false,
    val minFreeMemMb: Double = 256.0,
)

enum class Level { GREEN, YELLOW, RED }

object Safety {

    /** 读取手机实时资源。 */
    fun read(ctx: Context): Resources {
        val r = Resources()
        r.cores = Runtime.getRuntime().availableProcessors()

        // 内存：ActivityManager.MemoryInfo
        val am = ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val mi = ActivityManager.MemoryInfo()
        am.getMemoryInfo(mi)
        r.memTotalMb = mi.totalMem / 1048576.0
        r.memFreeMb = mi.availMem / 1048576.0
        if (r.memTotalMb > 0) r.mem = (1.0 - mi.availMem.toDouble() / mi.totalMem) * 100.0

        // 电池：sticky broadcast
        try {
            val bm = ctx.getSystemService(Context.BATTERY_SERVICE) as BatteryManager
            r.battery = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
            val status = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            if (status != null) {
                val st = status.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
                r.charging = st == BatteryManager.BATTERY_STATUS_CHARGING ||
                        st == BatteryManager.BATTERY_STATUS_FULL
                val tempRaw = status.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1)
                if (tempRaw > 0) r.temp = tempRaw / 10.0  // 单位 0.1℃
            }
        } catch (_: Exception) {}

        // CPU 占用：读 /proc/stat 两次差分（部分新系统受限，失败则 0）
        r.cpu = readCpuPercent()
        return r
    }

    private var lastIdle = 0L
    private var lastTotal = 0L
    private fun readCpuPercent(): Double {
        return try {
            RandomAccessFile("/proc/stat", "r").use { raf ->
                val line = raf.readLine() ?: return 0.0
                val toks = line.split(Regex("\\s+")).drop(1).mapNotNull { it.toLongOrNull() }
                if (toks.size < 4) return 0.0
                val idle = toks[3]
                val total = toks.sum()
                val dIdle = idle - lastIdle
                val dTotal = total - lastTotal
                lastIdle = idle; lastTotal = total
                if (dTotal <= 0) 0.0 else ((1.0 - dIdle.toDouble() / dTotal) * 100.0).coerceIn(0.0, 100.0)
            }
        } catch (_: Exception) { 0.0 }
    }

    /** 阈值判定 → (档位, 原因列表)。 */
    fun evaluate(r: Resources, t: Thresholds): Pair<Level, List<String>> {
        var level = Level.GREEN
        val reasons = mutableListOf<String>()
        fun bump(l: Level, why: String) {
            if (l.ordinal > level.ordinal) level = l
            reasons.add(why)
        }
        if (r.cpu >= t.cpuRed) bump(Level.RED, "CPU ${r.cpu.toInt()}% ≥ ${t.cpuRed.toInt()}%")
        else if (r.cpu >= t.cpuYellow) bump(Level.YELLOW, "CPU ${r.cpu.toInt()}%")

        if (r.mem >= t.memRed) bump(Level.RED, "内存 ${r.mem.toInt()}% ≥ ${t.memRed.toInt()}%")
        else if (r.mem >= t.memYellow) bump(Level.YELLOW, "内存 ${r.mem.toInt()}%")

        if (r.memFreeMb < t.minFreeMemMb) bump(Level.RED, "空闲内存仅 ${r.memFreeMb.toInt()}MB")

        r.temp?.let {
            if (it >= t.tempRed) bump(Level.RED, "电池温度 ${it}℃ ≥ ${t.tempRed}℃(过热保护)")
            else if (it >= t.tempYellow) bump(Level.YELLOW, "电池温度 ${it}℃")
        }
        if (r.battery <= t.batteryRed) bump(Level.RED, "电量 ${r.battery}% ≤ ${t.batteryRed}%")
        else if (r.battery <= t.batteryYellow) bump(Level.YELLOW, "电量 ${r.battery}%")

        if (t.requireCharging && !r.charging) bump(Level.RED, "未充电(策略要求充电才贡献)")

        if (reasons.isEmpty()) reasons.add("全部正常")
        return level to reasons
    }

    /** 是否可接任务。RED 一律拒；YELLOW 只接 small。 */
    fun canAccept(r: Resources, t: Thresholds, weight: String): Boolean {
        val (lvl, _) = evaluate(r, t)
        return when (lvl) {
            Level.RED -> false
            Level.YELLOW -> weight == "small"
            Level.GREEN -> true
        }
    }
}
