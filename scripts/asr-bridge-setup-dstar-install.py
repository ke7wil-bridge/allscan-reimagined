#!/usr/bin/env python3
"""Transactional installer for ASR-managed D-Star bridges."""
from __future__ import annotations
import hashlib,importlib.util,json,os,stat,subprocess,sys,tempfile,time
from pathlib import Path
from typing import Any
HERE=Path(__file__).resolve().parent
RUNTIME_SOURCE=Path(os.environ.get("ASR_DSTAR_RUNTIME_SOURCE","/usr/local/share/allscan-reimagined/runtime/dstar"))
STATE="/var/lib/allscan-reimagined/bridge-setup/installations"; SERVICE="allscan-reimagined-dstar.service"; UNIT=f"/etc/systemd/system/{SERVICE}"
class InstallError(RuntimeError): pass
def load(name,filename):
 s=importlib.util.spec_from_file_location(name,HERE/filename)
 if not s or not s.loader: raise InstallError(f"missing {filename}")
 m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);return m
planner=load("dstar_installer_plan","asr-bridge-setup-dstar.py"); sources=load("dstar_runtime_sources","asr-bridge-runtime-sources.py")
def rooted(root,logical):
 if not logical.startswith("/") or ".." in Path(logical).parts: raise InstallError("unsafe managed path")
 p=root/logical.lstrip("/"); cur=root.resolve()
 for part in p.relative_to(root).parts:
  cur/=part
  if cur.is_symlink(): raise InstallError(f"symbolic link in managed path: {logical}")
 return p
def atomic(path,data,mode):
 path.parent.mkdir(parents=True,exist_ok=True); fd,name=tempfile.mkstemp(prefix=f".{path.name}.",dir=path.parent)
 try:
  with os.fdopen(fd,"wb") as h:
   h.write(data);h.flush();os.fchmod(h.fileno(),mode)
   if path.exists(): st=path.stat();os.fchown(h.fileno(),st.st_uid,st.st_gid)
   os.fsync(h.fileno())
  os.replace(name,path)
 finally: Path(name).unlink(missing_ok=True)
def compose_yaml(plan):
 root=plan["resources"]["root"]; p=plan["ports"]
 return f"""services:
  dstar:
    image: allscan-reimagined/dstar:managed
    container_name: asr-dstar-bridge
    network_mode: host
    restart: unless-stopped
    environment:
      DSTAR_VOCODER_PORT: "{p['vocoder']}"
    volumes:
      - {root}/config:/config:ro
      - /var/log/dstar-mmdvm:/var/log/mmdvm
      - /var/log/dstar-ircddbgateway:/var/log/ircddbgateway
      - /run/dstar-bridge:/run/dstar
    healthcheck:
      test: ["CMD-SHELL", "test -s /run/dstar/health && test $(( $(date +%s) - $(cat /run/dstar/health) )) -lt 20"]
      interval: 10s
      timeout: 3s
      retries: 9
      start_period: 15s
"""
def unit_text(plan):
 compose=plan["resources"]["compose"]
 return f"""[Unit]
Description=ASR managed D-Star bridge
After=docker.service network-online.target asterisk.service
Requires=docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/mkdir -p /var/log/dstar-mmdvm /var/log/dstar-ircddbgateway /run/dstar-bridge
ExecStart=/usr/bin/docker compose -f {compose} up -d
ExecStop=/usr/bin/docker compose -f {compose} down
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
"""
def file_spec(root,plan):
 out=planner.integration_files(root,plan);out[plan["resources"]["compose"]]=(compose_yaml(plan).encode(),0o644);out[UNIT]=(unit_text(plan).encode(),0o644);return out
def build_image():
 if not RUNTIME_SOURCE.is_dir(): raise InstallError("D-Star runtime source is not installed")
 a=sources.binary_artifacts(); analog=a["Analog_Bridge"]; mmdvm=a["MMDVM_Bridge"]; tag="allscan-reimagined/dstar:managed"
 old=subprocess.run(["docker","image","inspect",tag,"--format","{{.Id}}"],capture_output=True,text=True); prior=old.stdout.strip() if old.returncode==0 else ""
 cmd=["docker","build","-t",tag,"--build-arg",f"ANALOG_URL={analog['url']}","--build-arg",f"ANALOG_SHA256={analog['sha256']}","--build-arg",f"MMDVM_URL={mmdvm['url']}","--build-arg",f"MMDVM_SHA256={mmdvm['sha256']}",str(RUNTIME_SOURCE)]
 r=subprocess.run(cmd,capture_output=True,text=True)
 if r.returncode: raise InstallError(f"D-Star runtime build failed: {r.stdout[-300:]} {r.stderr[-700:]}")
 return prior
def restore_image(prior):
 tag="allscan-reimagined/dstar:managed"
 if prior: subprocess.run(["docker","image","tag",prior,tag],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 else: subprocess.run(["docker","image","rm","-f",tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def verify_runtime(plan):
 r=subprocess.run(["docker","inspect","-f","{{.State.Running}}","asr-dstar-bridge"],capture_output=True,text=True)
 if r.returncode or r.stdout.strip()!="true": raise InstallError("D-Star container did not start")
 last=""
 for _ in range(60):
  h=subprocess.run(["docker","inspect","-f","{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}","asr-dstar-bridge"],capture_output=True,text=True);last=h.stdout.strip()
  if h.returncode==0 and last=="healthy": break
  time.sleep(1)
 else: raise InstallError(f"D-Star provisioning health did not pass: {last or 'unknown'}")
 node=plan["bridgeNode"]
 for _ in range(20):
  a=subprocess.run(["asterisk","-rx",f"rpt stats {node}"],capture_output=True,text=True)
  if a.returncode==0 and f"NODE {node} STATISTICS" in a.stdout:return
  time.sleep(.5)
 raise InstallError(f"Asterisk did not register D-Star transport node {node}")
def install(root,settings,*,expected_digest,manage_services=False,fail_after=None):
 if manage_services and root!=Path("/"): raise InstallError("service management is only valid for the real host")
 plan=planner.plan(root,settings)
 if plan["digest"]!=expected_digest: raise InstallError("preview is stale; preview again")
 outputs=file_spec(root,plan); identity=settings.bridge_id; record=f"{STATE}/{identity}.json"; rec=rooted(root,record)
 old_record=json.loads(rec.read_text()) if rec.is_file() else None
 if old_record and old_record.get("bridgeType")!="dstar": raise InstallError("existing installation belongs to another bridge type")
 allowed=set(old_record.get("ownedFiles",[])) if old_record else set()
 for logical in (*outputs,record):
  t=rooted(root,logical)
  if t.exists() and logical not in (planner.CONFIG,planner.RPT,planner.MODULES,record) and logical not in allowed: raise InstallError(f"existing file is not owned by this bridge: {logical}")
  if t.exists() and (not t.is_file() or t.stat().st_nlink!=1): raise InstallError(f"managed path is not a regular single-link file: {logical}")
 before={x:(rooted(root,x).read_bytes(),stat.S_IMODE(rooted(root,x).stat().st_mode)) if rooted(root,x).is_file() else None for x in (*outputs,record)}
 owners={x:(rooted(root,x).stat().st_uid,rooted(root,x).stat().st_gid) if rooted(root,x).is_file() else None for x in (*outputs,record)}
 backup=rooted(root,f"/var/lib/allscan-reimagined/bridge-setup/backups/{time.time_ns()}-{identity}");backup.mkdir(parents=True,mode=0o700)
 atomic(backup/"manifest.json",(json.dumps({k:{"existed":v is not None,"sha256":hashlib.sha256(v[0]).hexdigest() if v else None} for k,v in before.items()},indent=2,sort_keys=True)+"\n").encode(),0o600)
 prior="";enabled=active=False;changed=0;asterisk_changed=False
 try:
  for logical,(data,mode) in outputs.items():
   if before[logical]==(data,mode):continue
   atomic(rooted(root,logical),data,mode);changed+=1;asterisk_changed|=logical in (planner.RPT,planner.MODULES)
   if fail_after is not None and changed>=fail_after: raise InstallError("injected apply failure")
  if manage_services:
   enabled=subprocess.run(["systemctl","is-enabled","--quiet",SERVICE]).returncode==0;active=subprocess.run(["systemctl","is-active","--quiet",SERVICE]).returncode==0
   prior=build_image();subprocess.run(["systemctl","daemon-reload"],check=True)
   if asterisk_changed:subprocess.run(["systemctl","restart","asterisk.service"],check=True,capture_output=True,text=True)
   subprocess.run(["systemctl","enable","--now",SERVICE],check=True,capture_output=True,text=True);verify_runtime(plan)
  owned=sorted(x for x in outputs if x not in (planner.CONFIG,planner.RPT,planner.MODULES))
  rd={"schema":1,"bridgeType":"dstar","bridgeId":identity,"bridgeNode":plan["bridgeNode"],"ports":plan["ports"],"service":SERVICE,"provisioningVerified":manage_services,"liveAudioVerified":False,"committedAt":old_record.get("committedAt",int(time.time())) if old_record else int(time.time()),"ownedFiles":owned}
  atomic(rec,(json.dumps(rd,indent=2,sort_keys=True)+"\n").encode(),0o600)
  return {"ok":True,"digest":plan["digest"],"bridgeId":identity,"bridgeNode":plan["bridgeNode"],"service":SERVICE,"changedFiles":changed,"backup":str(backup),"provisioningVerified":manage_services,"liveAudioVerified":False}
 except Exception:
  rollback_error=None
  if manage_services:subprocess.run(["systemctl","disable","--now",SERVICE],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  try:
   for logical,orig in reversed(list(before.items())):
    t=rooted(root,logical)
    if orig is None:t.unlink(missing_ok=True)
    else:
     atomic(t,orig[0],orig[1])
     if owners[logical] is not None:os.chown(t,*owners[logical])
   for logical,orig in before.items():
    t=rooted(root,logical)
    if orig is None and t.exists():raise InstallError(f"rollback verification failed: {logical}")
    if orig is not None and (not t.is_file() or t.read_bytes()!=orig[0] or stat.S_IMODE(t.stat().st_mode)!=orig[1]):raise InstallError(f"rollback verification failed: {logical}")
  except Exception as e:rollback_error=e
  if manage_services:
   subprocess.run(["systemctl","daemon-reload"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   if asterisk_changed:subprocess.run(["systemctl","restart","asterisk.service"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   try:
    restore_image(prior)
    if enabled:subprocess.run(["systemctl","enable",SERVICE],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if active:subprocess.run(["systemctl","start",SERVICE],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   except Exception as e:rollback_error=rollback_error or e
  if rollback_error:raise rollback_error
  raise
