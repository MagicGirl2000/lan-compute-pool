package com.example.cpu

import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.cpu.ui.theme.安卓手机算力租用全部包含安全阈值Theme
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {

    private lateinit var worker: Worker

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val androidId = try {
            android.provider.Settings.Secure.getString(
                contentResolver, android.provider.Settings.Secure.ANDROID_ID
            ) ?: "0"
        } catch (e: Exception) { "0" }
        val deviceId = "android-" + (Build.MODEL ?: "phone").replace(" ", "_") +
                "-" + androidId.take(6)
        val deviceName = Build.MODEL ?: "Android Phone"
        // 默认 10.0.2.2 = Android 模拟器(AVD)访问宿主机 PC 的固定 IP。
        // 真机用同一局域网时改成 PC 的局域网 IP(如 192.168.1.x)。
        worker = Worker(this, "http://10.0.2.2:9000", deviceId, deviceName)

        setContent {
            安卓手机算力租用全部包含安全阈值Theme {
                WorkerScreen(worker, deviceName)
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        worker.stop()
    }
}

@Composable
fun WorkerScreen(worker: Worker, deviceName: String) {
    var tick by remember { mutableStateOf(0) }
    var serverUrl by remember { mutableStateOf(worker.serverUrl) }
    var requireCharging by remember { mutableStateOf(worker.requireCharging) }
    var running by remember { mutableStateOf(worker.running) }

    // 每秒刷新 UI（tick 被读取以触发重组）
    LaunchedEffect(Unit) {
        worker.onUpdate = { tick++ }
        while (true) { delay(1000); tick++ }
    }
    @Suppress("UNUSED_EXPRESSION") tick  // 触发重组依赖

    val res = worker.lastRes
    val level = worker.lastLevel
    val levelColor = when (level) {
        Level.GREEN -> Color(0xFF44CC88)
        Level.YELLOW -> Color(0xFFFFCC55)
        Level.RED -> Color(0xFFFF6666)
    }

    Scaffold(modifier = Modifier.fillMaxSize()) { pad ->
        Column(
            Modifier
                .padding(pad)
                .padding(16.dp)
                .fillMaxSize()
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text("⚡ 算力分享 Worker", fontSize = 22.sp, fontWeight = FontWeight.Bold)
            Text("$deviceName · 把本机算力贡献给你的 PC 项目加速",
                fontSize = 12.sp, color = Color.Gray)

            // 安全阈值档位
            ElevatedCard {
                Column(Modifier.padding(14.dp)) {
                    Text("● 安全阈值：${level.name}",
                        color = levelColor, fontWeight = FontWeight.Bold, fontSize = 16.sp)
                    Spacer(Modifier.height(4.dp))
                    worker.lastReasons.forEach {
                        Text("· $it", fontSize = 12.sp, color = Color.Gray)
                    }
                }
            }

            // 实时资源
            ElevatedCard {
                Column(Modifier.padding(14.dp)) {
                    Text("本机资源", fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(8.dp))
                    MetricBar("CPU", res.cpu)
                    MetricBar("内存", res.mem)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "电量 ${res.battery}% ${if (res.charging) "⚡充电中" else ""}  " +
                                (res.temp?.let { "· 电池 ${it}℃ " } ?: "") +
                                "· ${res.cores}核 · ${(res.memTotalMb / 1024).toInt()}GB",
                        fontSize = 12.sp, color = Color.Gray
                    )
                }
            }

            // 连接设置
            ElevatedCard {
                Column(Modifier.padding(14.dp)) {
                    Text("PC 协调端地址", fontWeight = FontWeight.Bold)
                    OutlinedTextField(
                        value = serverUrl,
                        onValueChange = { serverUrl = it; worker.serverUrl = it },
                        singleLine = true,
                        label = { Text("http://PC局域网IP:9000") },
                        modifier = Modifier.fillMaxWidth()
                    )
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Switch(checked = requireCharging, onCheckedChange = {
                            requireCharging = it; worker.requireCharging = it
                        })
                        Text(" 仅充电时贡献（保护电池，推荐开）", fontSize = 12.sp)
                    }
                }
            }

            // 状态 + 统计
            ElevatedCard {
                Column(Modifier.padding(14.dp)) {
                    Text("状态：${worker.statusLine}", fontSize = 13.sp)
                    Text("已完成任务：${worker.jobsDone} · 本会话流量 " +
                            "${"%.2f".format(worker.netSessionMb)} MB",
                        fontSize = 12.sp, color = Color.Gray)
                }
            }

            // 启停
            Button(
                onClick = {
                    if (running) worker.stop() else worker.start()
                    running = worker.running
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (running) Color(0xFFCC5555) else Color(0xFF44AA66)
                )
            ) {
                Text(if (running) "■ 停止贡献算力" else "▶ 开始贡献算力", fontSize = 16.sp)
            }

            Text(
                "说明：本 app 只把算力贡献给你自己的 PC 协调端（局域网），加速你的项目。\n" +
                        "不会也无法把算力贡献给 Claude/Anthropic 换额度——那种通道不存在。\n" +
                        "全程受安全阈值保护：电量低/未充电/过热/内存紧张会自动暂停。",
                fontSize = 11.sp, color = Color.Gray
            )
        }
    }
}

@Composable
fun MetricBar(label: String, value: Double) {
    val pct = (value / 100.0).coerceIn(0.0, 1.0)
    val color = when {
        pct >= 0.9 -> Color(0xFFFF6666)
        pct >= 0.75 -> Color(0xFFFFCC55)
        else -> Color(0xFF44CC88)
    }
    Column(Modifier.padding(vertical = 3.dp)) {
        Row {
            Text(label, fontSize = 12.sp, modifier = Modifier.width(40.dp))
            Text("${value.toInt()}%", fontSize = 12.sp, color = color)
        }
        LinearProgressIndicator(
            progress = { pct.toFloat() },
            modifier = Modifier
                .fillMaxWidth()
                .height(8.dp)
                .clip(RoundedCornerShape(4.dp)),
            color = color,
            trackColor = Color(0xFF2A3040)
        )
    }
}
