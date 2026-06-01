package com.example.cpu

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat

/**
 * 算力前台服务 (beta0.3)
 * ======================
 * 解决"锁屏→后台被冻/杀→交不出活=假工分"的问题：
 *   - 前台服务 + 常驻通知：让系统不轻易杀掉本进程。
 *   - PARTIAL_WAKE_LOCK：屏幕灭了 CPU 仍在跑，矿工线程不被冻住，能继续交【真活】。
 *
 * 设计：服务不持有 Worker（Worker 仍由 MainActivity 创建、UI 直接读它）；
 * 服务只负责"保活进程 + 持唤醒锁"。Worker 是进程内的守护线程，进程活着它就活着。
 * MainActivity 在"开始/停止贡献"时一并启动/停止本服务。
 */
class WorkerService : Service() {

    companion object {
        const val CHANNEL = "cpu_worker"
        const val NOTIF_ID = 1001
        @Volatile var statusProvider: (() -> String)? = null   // 让通知能显示实时状态
    }

    private var wakeLock: PowerManager.WakeLock? = null
    private var ticker: Thread? = null
    @Volatile private var alive = false

    override fun onCreate() {
        super.onCreate()
        createChannel()
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "cpu:worker").apply {
            setReferenceCounted(false)
            try { acquire() } catch (_: Exception) {}
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForeground(NOTIF_ID, buildNotif("正在贡献算力（锁屏也继续）"))
        // 后台刷新通知文案，显示矿工实时状态
        alive = true
        if (ticker == null) {
            ticker = Thread {
                while (alive) {
                    try {
                        val s = statusProvider?.invoke() ?: "正在贡献算力"
                        nm().notify(NOTIF_ID, buildNotif(s))
                        Thread.sleep(3000)
                    } catch (_: Exception) { Thread.sleep(3000) }
                }
            }.apply { isDaemon = true; start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        alive = false
        try { wakeLock?.release() } catch (_: Exception) {}
        wakeLock = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun nm() = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm().createNotificationChannel(
                NotificationChannel(CHANNEL, "算力分享", NotificationManager.IMPORTANCE_LOW)
            )
        }
    }

    private fun buildNotif(text: String): Notification =
        NotificationCompat.Builder(this, CHANNEL)
            .setContentTitle("⚡ 算力分享 Worker")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_manage)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
}
