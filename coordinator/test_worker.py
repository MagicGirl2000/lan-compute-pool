# -*- coding: utf-8 -*-
"""本机模拟 worker 跑一轮，验证协调端 register→pull→run→complete 全链路。"""
import json, time, urllib.request, hashlib, math, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

BASE = "http://127.0.0.1:9000"
DID = "pc-test-worker"

def post(path, body):
    req = urllib.request.Request(BASE+path, data=json.dumps(body).encode(),
                                 headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())

res = {"cpu":10,"mem":50,"mem_free_mb":8000,"mem_total_mb":32000,"cores":16,
       "battery":100,"charging":True}
base = {"device_id":DID,"name":"PC测试Worker","kind":"pc","net_session_mb":0,
        "resources":res,"caps":{"cpu":True}}

print("注册:", post("/api/register", base)["level"])

def run_job(job):
    t=job["type"]; p=job.get("payload",{})
    if t=="prime":
        lim=p.get("limit",100000); c=0
        for k in range(2,lim+1):
            isp=True; d=2
            while d*d<=k:
                if k%d==0: isp=False;break
                d+=1
            if isp:c+=1
        return {"primes":c,"limit":lim}
    if t=="hash":
        n=p.get("count",100000); h=p.get("seed","s").encode()
        for _ in range(n): h=hashlib.sha256(h).digest()
        return {"hash":h.hex()[:16],"rounds":n}
    if t=="compute":
        it=p.get("iterations",1000000); acc=0.0
        for i in range(it): acc+=math.sqrt((i%1000)+1)
        return {"acc":acc,"iterations":it}
    return {"echo":p.get("msg","")}

done=0
for _ in range(10):
    r = post("/api/pull_job", base)
    job = r.get("job")
    if not job:
        if r.get("paused"): print("被安全阈值暂停"); break
        print("无更多任务"); break
    print(f"领取 {job['id']} ({job['type']}) ...", end=" ")
    t0=time.time()
    result = run_job(job)
    cpu_sec=(time.time()-t0)*res["cores"]
    post("/api/complete_job", {"device_id":DID,"job_id":job["id"],"result":result,
         "usage":{"cpu_core_sec":cpu_sec,"mem_mb_hour":0.5,"net_mb":0.05,"gpu_sec":0}})
    print(f"完成 {result}  (CPU {cpu_sec:.1f}核秒)")
    done+=1
print(f"\n本轮完成 {done} 个任务")
