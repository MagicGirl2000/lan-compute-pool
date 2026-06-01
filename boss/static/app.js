// 算力老板 控制台前端 / Compute Boss console frontend
const $ = (id) => document.getElementById(id);
const COLORS = ["#4ea1ff", "#42d392", "#ffcc52", "#7c5cff", "#ff8a5c", "#5cc8ff"];
let PROJECTS = [];

function clsByPct(p, y = 75, r = 90) { return p >= r ? "r" : p >= y ? "y" : ""; }
function devCpu(p) {  // 手机CPU显示：优先工人进程占用(恒可读)，否则系统CPU，再否则 —
  const v = (p.cpu_proc && p.cpu_proc > 0) ? p.cpu_proc : ((p.cpu && p.cpu > 0) ? p.cpu : null);
  return v != null ? v.toFixed(0) + "%" : "—";
}
function metric(lbl, pct, txt) {
  pct = Math.min(100, pct || 0);
  return `<div class="metric"><span class="lbl">${lbl}</span>
    <span class="bar"><i class="${clsByPct(pct)}" style="width:${pct}%"></i></span>
    <span class="val">${txt != null ? txt : pct.toFixed(0) + "%"}</span></div>`;
}

// ── 项目列表 ────────────────────────────────────────────────────────────────
async function loadProjects() {
  const r = await (await fetch("/api/projects")).json();
  PROJECTS = r.projects;
  $("projSel").innerHTML = PROJECTS.map(p => `<option value="${p.key}">${p.title}</option>`).join("");
  syncProjDesc();
}
function syncProjDesc() {
  const p = PROJECTS.find(x => x.key === $("projSel").value);
  if (!p) return;
  $("projDesc").textContent = p.desc;
  if (!$("projParams").value.trim()) $("projParams").value = JSON.stringify(p.params);
}
$("projSel").addEventListener("change", () => { $("projParams").value = ""; syncProjDesc(); });

// ── 主轮询 ──────────────────────────────────────────────────────────────────
async function overview() {
  let s;
  try { s = await (await fetch("/api/overview")).json(); }
  catch { return; }

  // 协调端
  const on = s.coordinator.online;
  $("coordDot").className = "dot" + (on ? " on" : "");
  $("coordText").textContent = on ? `协调端在线 ${s.coordinator.url}` : `协调端离线 ${s.coordinator.url}`;
  $("lanText").textContent = `本机 ${s.lan_ip}:${s.config.boss_port}`;
  if (s.version) $("verBadge").textContent = s.version;
  if (s.dev_caps) {
    const c = s.dev_caps;
    $("devCaps").textContent = `本机能力: ${c.build?"✓构建APK":"✗无SDK"} · ${c.adb?"✓adb":"✗无adb"} · ✓pip · ✓下载`;
  }

  // PC 硬件
  const pf = s.profile, u = s.util;
  $("pcSpec").textContent =
    `${pf.cpu.physical}物理/${pf.cpu.logical}逻辑核 · ${pf.cpu.freq_mhz}MHz · ${(pf.mem.total_mb/1024).toFixed(1)}GB · 盘${pf.disk.total_gb}GB`
    + (pf.gpus.length ? ` · GPU ${pf.gpus.map(g=>g.name).join(",")}` : " · 无独显");
  let m = metric("CPU", u.cpu) + metric("内存", u.mem) + metric("磁盘", u.disk);
  (u.gpus || []).forEach(g => { m += metric("GPU" + g.index, g.gpu); });
  m += `<div class="metric"><span class="lbl">网络</span><span class="val" style="width:auto;color:var(--mut)">↑${u.net_kbps_up||0} ↓${u.net_kbps_down||0} KB/s</span></div>`;
  $("pcMetrics").innerHTML = m;
  $("perCpu").innerHTML = (u.per_cpu || []).map(p =>
    `<div class="c"><i style="height:${Math.min(100,p)}%"></i></div>`).join("");

  // 手机
  const ph = s.coordinator.phones || [];
  $("phones").innerHTML = ph.length ? ph.map(p =>
    `<div class="phone"><span class="nm">${p.name}</span>
      <span class="lv ${p.level}">${p.level}</span>
      <span>${p.cores||"?"}核</span>
      <span title="安卓限制读全局CPU,优先显示工人进程占用">CPU ${devCpu(p)}</span>
      <span>${p.battery!=null?"🔋"+p.battery+"%"+(p.charging?"⚡":""):""}</span></div>`
  ).join("") : `<div class="empty">暂无手机接入。手机算力 app 填本机IP <b>${s.lan_ip}:9000</b>（协调端）即可。</div>`;

  // 当前运行
  renderRun(s.run, s.busy);
}

// ── 算力比例 ────────────────────────────────────────────────────────────────
function renderRatio(devices) {
  const ids = Object.keys(devices);
  if (!ids.length) { $("ratioBar").innerHTML = '<div class="empty" style="margin:auto">尚未评估</div>'; $("ratioLegend").innerHTML = ""; return; }
  $("ratioBar").innerHTML = ids.map((id, i) => {
    const d = devices[id], pct = (d.ratio * 100);
    return `<div class="seg" style="width:${pct}%;background:${COLORS[i%COLORS.length]}" title="${d.name}">${pct>=8?d.name+" "+pct.toFixed(0)+"%":""}</div>`;
  }).join("");
  $("ratioLegend").innerHTML = ids.map((id, i) => {
    const d = devices[id];
    return `<span class="it"><span class="sw" style="background:${COLORS[i%COLORS.length]}"></span>
      ${d.name}：${(d.ratio*100).toFixed(1)}% · 评分 ${d.score.toFixed(2)}</span>`;
  }).join("");
}

$("btnAssess").addEventListener("click", async () => {
  $("btnAssess").disabled = true; $("btnAssess").textContent = "评估中…";
  try {
    const r = await (await fetch("/api/assess", { method: "POST" })).json();
    if (r.ok) renderRatio(r.devices); else alert("评估失败：" + r.error);
  } finally { $("btnAssess").disabled = false; $("btnAssess").textContent = "重新评估算力"; }
});

// ── 运行渲染 ────────────────────────────────────────────────────────────────
function renderRun(run, busy) {
  $("btnRun").disabled = busy;
  $("btnCancel").disabled = !busy;
  if (!run) return;
  if (run.ratio && run.devices && Object.keys(run.devices).length) renderRatio(run.devices);

  const pct = (run.progress * 100) || 0;
  $("runBar").style.width = pct + "%";
  $("runPct").textContent = pct.toFixed(0) + "%";
  const st = run.status || "idle";
  $("runStatus").className = "pill " + st;
  $("runStatus").textContent = { preparing:"准备", assessing:"评估算力", running:"运行中", done:"完成", error:"出错", cancelled:"已取消", idle:"空闲" }[st] || st;
  $("runThru").textContent = run.throughput ? `吞吐 ${run.throughput} 片/秒` : "";
  $("runElapsed").textContent = run.elapsed_s ? `耗时 ${run.elapsed_s}s · ${run.done_items}/${run.total_items}` : "";

  // 任务流
  const devs = run.devices || {};
  $("taskflow").innerHTML = Object.keys(devs).map(id => {
    const d = devs[id], assigned = d.assigned || 0, done = d.done || 0;
    const p = assigned ? (done / assigned * 100) : 0;
    const ic = d.kind === "pc" ? "💻" : "📱";
    return `<div class="flow"><span class="who"><span class="ic">${ic}</span>${d.name}</span>
      <span class="track"><i class="${d.kind==='pc'?'pc':''}" style="width:${p}%"></i></span>
      <span class="num">${done}/${assigned}</span></div>`;
  }).join("") || '<div class="empty">无设备分配</div>';

  $("runResult").textContent = run.result ? JSON.stringify(run.result, null, 2) : "—";
  $("runLog").textContent = (run.log || []).map(l => `[${l.t}s] ${l.msg}`).join("\n") || "—";
}

$("btnRun").addEventListener("click", async () => {
  let params = {};
  try { params = JSON.parse($("projParams").value || "{}"); }
  catch { alert("参数 JSON 格式错误"); return; }
  const r = await (await fetch("/api/run", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project: $("projSel").value, params })
  })).json();
  if (!r.ok) alert("启动失败：" + r.msg);
});
$("btnCancel").addEventListener("click", () => fetch("/api/cancel", { method: "POST" }));

// ── 开发任务加速 (beta0.2) ───────────────────────────────────────────────────
const DEV_FIELDS = {
  parallel_compute: [
    { k: "project", label: "项目", val: "prime_scan", ph: "prime_scan / hash_grind / montecarlo / local_model" },
    { k: "params", label: "参数JSON", val: '{"limit":2000000,"chunk":50000}', ph: '{"limit":2000000}' },
  ],
  install_pip: [
    { k: "packages", label: "包名(空格分隔)", val: "", ph: "numpy pandas requests flask" },
    { k: "requirements", label: "或 requirements 路径", val: "", ph: "D:\\proj\\requirements.txt" },
  ],
  install_plugin: [
    { k: "urls", label: "下载URL(空格/换行分隔)", val: "", ph: "https://.../a.zip  https://.../b.jar" },
  ],
  build_apk: [
    { k: "project_dir", label: "安卓工程目录", val: "", ph: "D:\\proj\\MyApp" },
    { k: "task", label: "Gradle任务", val: "assembleDebug", ph: "assembleDebug / assembleRelease" },
  ],
  deploy_apk: [
    { k: "apk", label: "APK路径(留空=用工程最新产物)", val: "", ph: "D:\\proj\\app\\build\\outputs\\apk\\debug\\app-debug.apk" },
    { k: "project_dir", label: "或工程目录", val: "", ph: "D:\\proj\\MyApp" },
  ],
  update_workers: [
    { k: "worker_apk", label: "工人APK路径", val: "", ph: "D:\\proj\\worker-android\\app\\build\\outputs\\apk\\release\\app-release.apk" },
  ],
};
function curDevTask() {
  const r = document.querySelector('input[name="devtask"]:checked');
  return r ? r.value : "parallel_compute";
}
function renderDevFields() {
  const t = curDevTask();
  const fs = DEV_FIELDS[t] || [];
  $("devFields").innerHTML = fs.map(f =>
    `<label class="fl"><span>${f.label}</span>
      <input data-k="${f.k}" value="${f.val.replace(/"/g,'&quot;')}" placeholder="${f.ph}"></label>`).join("");
}
document.querySelectorAll('input[name="devtask"]').forEach(r =>
  r.addEventListener("change", renderDevFields));

function devParams() {
  const t = curDevTask();
  const p = {};
  $("devFields").querySelectorAll("input").forEach(i => {
    const v = i.value.trim(); if (v) p[i.dataset.k] = v;
  });
  if (t === "parallel_compute") {
    let pj = p.project || "prime_scan", pr = {};
    try { pr = JSON.parse(p.params || "{}"); } catch { alert("参数JSON格式错误"); throw 0; }
    return { project: pj, params: pr };
  }
  return p;
}
function devResources() {
  const r = {};
  document.querySelectorAll("input.res").forEach(c => r[c.value] = c.checked);
  return r;
}
$("btnDevRun").addEventListener("click", async () => {
  let params; try { params = devParams(); } catch { return; }
  const body = { task: curDevTask(), params, resources: devResources() };
  const r = await (await fetch("/api/dev/run", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  })).json();
  if (!r.ok) alert("启动失败：" + r.msg);
});
$("btnDevCancel").addEventListener("click", () => fetch("/api/dev/cancel", { method: "POST" }));

async function devTick() {
  let s; try { s = await (await fetch("/api/dev/status")).json(); } catch { return; }
  const st = s.status || "idle";
  const busy = st === "running";
  $("btnDevRun").disabled = busy;
  $("btnDevCancel").disabled = !busy;
  $("devStat").className = "pill " + st;
  $("devStat").textContent = { idle:"空闲", running:"运行中", done:"完成 ✓", error:"出错", cancelled:"已取消" }[st] || st;
  // 指标
  const m = s.metrics || {};
  let parts = [];
  if (s.elapsed_s) parts.push(s.elapsed_s + "s");
  if (m.download_s != null) parts.push(`下载${m.download_s}s`);
  if (m.install_s != null) parts.push(`安装${m.install_s}s`);
  if (m.wheelhouse_files != null) parts.push(`wheelhouse ${m.wheelhouse_files}`);
  if (m.wall_s != null) parts.push(`构建${m.wall_s}s${m.cold?"(冷)":"(缓存)"}`);
  if (m.apks != null) parts.push(`APK×${m.apks}`);
  if (m.files != null) parts.push(`下载${m.files}个 ${m.total_mb||0}MB`);
  if (m.by_device) parts.push("按设备:" + Object.entries(m.by_device).map(([k,v])=>`${k}×${v}`).join(" "));
  $("devMetrics").textContent = parts.join(" · ");
  // 日志（compute 模式用调度日志，命令模式用 dev log）
  $("devLog").textContent = (s.log || []).join("\n") || "—";
}

// ── 启动 ────────────────────────────────────────────────────────────────────
loadProjects();
renderDevFields();
overview();
devTick();
setInterval(overview, 1500);
setInterval(devTick, 1200);
