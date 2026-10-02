#!/usr/bin/env python3
"""Read-only planning and host rendering for ASR-managed D-Star."""
from __future__ import annotations
import hashlib,importlib.util,json,re,sys,zlib
from dataclasses import asdict,dataclass
from pathlib import Path
from typing import Any
HERE=Path(__file__).resolve().parent
CONFIG="/etc/allscan-reimagined/config.json"; RPT="/etc/asterisk/rpt.conf"; MODULES="/etc/asterisk/modules.conf"
STATE="/var/lib/allscan-reimagined/bridge-setup/installations"; ROOT="/opt/allscan-reimagined-bridges/dstar"
class PlanError(RuntimeError): pass
@dataclass(frozen=True)
class DStarSettings:
 bridge_id:str; callsign:str; dmr_id:int; reflector:str; module:str="A"; title:str="D-Star Bridge"; bridge_node:int|None=None
def load(name,filename):
 s=importlib.util.spec_from_file_location(name,HERE/filename)
 if not s or not s.loader: raise PlanError(f"cannot load {filename}")
 m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); return m
def rooted(root,logical): return root/logical.lstrip("/")
def safe_regular(root,logical):
 p=rooted(root,logical); cur=root.resolve()
 for part in p.relative_to(root).parts:
  cur/=part
  if cur.is_symlink(): raise PlanError(f"unsafe symbolic link in {logical}")
 if not p.is_file(): raise PlanError(f"required ASL3 file is missing: {logical}")
 return p
def validate(v):
 bid=v.bridge_id.strip().lower(); call=v.callsign.strip().upper(); refl=v.reflector.strip().upper(); mod=v.module.strip().upper(); title=v.title.strip()
 if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}",bid): raise PlanError("invalid bridge ID")
 if not re.fullmatch(r"[A-Z0-9]{3,8}",call): raise PlanError("invalid D-Star callsign")
 if not 1<=int(v.dmr_id)<=9999999: raise PlanError("invalid DMR ID")
 if not re.fullmatch(r"(?:XRF|XLX|REF|DCS)[0-9A-Z]{3,6}",refl): raise PlanError("invalid D-Star reflector")
 if not re.fullmatch(r"[A-Z]",mod): raise PlanError("invalid D-Star module")
 if not title or len(title)>80 or any(c in title for c in "\r\n\0"): raise PlanError("invalid bridge title")
 if v.bridge_node is not None and not 1001<=v.bridge_node<=1999: raise PlanError("private bridge node must be in 1001-1999")
 return DStarSettings(bid,call,int(v.dmr_id),refl,mod,title,v.bridge_node)
def ports(bid):
 b=44000+(zlib.crc32(bid.encode())&0xffffffff)%700
 return {"usrp_rx":b,"usrp_tx":b+1,"gateway":b+2,"mmdvm":b+3,"ambe_tx":b+4,"ambe_rx":b+5,"vocoder":b+6,"mqtt":b+7}
def plan(root,settings):
 settings=validate(settings); cp=safe_regular(root,CONFIG); rp=safe_regular(root,RPT); mp=safe_regular(root,MODULES)
 try: cfg=json.loads(cp.read_text())
 except Exception as e: raise PlanError("ASR configuration is invalid") from e
 core=load("asr_core_dstar","asr-bridge-setup-core.py"); facts=core.ASL3Adapter(root).detect()
 if root==Path("/"):
  if not facts.systemd or not facts.docker or not facts.docker_compose: raise PlanError("D-Star setup requires systemd and Docker Compose")
  mods=list((root/"usr/lib").glob("asterisk/modules/chan_usrp.so"))+list((root/"usr/lib").glob("*/asterisk/modules/chan_usrp.so"))
  if not mods: raise PlanError("Asterisk chan_usrp module is not installed")
 try: main=int(cfg["node"])
 except Exception as e: raise PlanError("main AllStar node is missing") from e
 bs=cfg.setdefault("bridges",[]); matches=[b for b in bs if isinstance(b,dict) and b.get("id")==settings.bridge_id]
 rec=rooted(root,f"{STATE}/{settings.bridge_id}.json")
 if len(matches)>1 or (matches and str(matches[0].get("mode","")).lower()!="dstar"): raise PlanError("bridge ID belongs to another or duplicate bridge")
 if matches:
  if not rec.is_file() or rec.is_symlink(): raise PlanError("cannot change an unowned D-Star bridge")
  node=int(matches[0]["node"])
 else:
  used=set(facts.node_numbers)|{main}
  for b in bs:
   try: used.add(int(b.get("node"))) if isinstance(b,dict) else None
   except Exception: pass
  node=settings.bridge_node or core.allocate_node(used)
  if node in used: raise PlanError("requested private node is already in use")
 ps=ports(settings.bridge_id)
 if not matches and set(ps.values())&set(facts.listening_udp_ports): raise PlanError("a required D-Star port is already in use")
 res={"root":ROOT,"compose":f"{ROOT}/compose.yml","configDir":f"{ROOT}/config","runtime":f"{ROOT}/runtime","config":CONFIG,"rpt":RPT,"modules":MODULES}
 out={"schema":1,"bridgeType":"dstar","settings":asdict(settings),"mainNode":main,"bridgeNode":node,"ports":ps,"resources":res,"image":"allscan-reimagined/dstar:managed","hostState":{"configSha256":hashlib.sha256(cp.read_bytes()).hexdigest(),"rptSha256":hashlib.sha256(rp.read_bytes()).hexdigest(),"modulesSha256":hashlib.sha256(mp.read_bytes()).hexdigest()}}
 out["digest"]=hashlib.sha256(json.dumps(out,sort_keys=True,separators=(",",":")).encode()).hexdigest(); return out
def irc_config(plan):
 s=plan["settings"]; p=plan["ports"]; target=f'{s["reflector"]} {s["module"]}'
 return f"""gatewayType=0
gatewayCallsign={s['callsign']}
gatewayAddress=0.0.0.0
icomAddress=127.0.0.1
icomPort={p['gateway']}
hbAddress=127.0.0.1
hbPort={p['gateway']}
repeaterCall1={s['callsign']}
repeaterBand1=B
repeaterType1=0
repeaterAddress1=127.0.0.1
repeaterPort1={p['mmdvm']}
reflector1={target}
atStartup1=1
reconnect1=1
ircddbEnabled=0
aprsEnabled=0
dextraEnabled=1
dextraMaxDongles=5
dplusEnabled=0
dcsEnabled=1
ccsEnabled=0
xlxEnabled=1
remoteEnabled=0
infoEnabled=1
echoEnabled=0
logEnabled=1
dtmfEnabled=1
mqttAddress=127.0.0.1
mqttPort={p['mqtt']}
mqttKeepalive=60
"""
def mmdvm_config(plan):
 s=plan["settings"]; p=plan["ports"]
 return f"""[General]
Callsign={s['callsign']}
Id={s['dmr_id']}
Timeout=180
Duplex=0
[Info]
RXFrequency=0
TXFrequency=0
Power=1
Latitude=0
Longitude=0
Height=0
Location=ASR
Description=ASR D-Star Bridge
URL=
[Log]
DisplayLevel=2
FileLevel=2
FilePath=/var/log/mmdvm
FileRoot=MMDVM_Bridge
[Modem]
Port=/dev/null
RSSIMappingFile=/dev/null
Trace=0
Debug=0
[D-Star]
Enable=1
Module=B
[DMR]
Enable=0
[System Fusion]
Enable=0
[P25]
Enable=0
[NXDN]
Enable=0
[D-Star Network]
Enable=1
GatewayAddress=127.0.0.1
GatewayPort={p['gateway']}
LocalPort={p['mmdvm']}
Debug=0
"""
def analog_config(plan):
 s=plan["settings"]; p=plan["ports"]
 return f"""[GENERAL]
logLevel=2
exportMetadata=true
decoderFallBack=true
useEmulator=false
[AMBE_AUDIO]
address=127.0.0.1
txPort={p['ambe_tx']}
rxPort={p['ambe_rx']}
ambeMode=DSTAR
minTxTimeMS=2500
gatewayDmrId={s['dmr_id']}
repeaterID={s['dmr_id']}01
txTg=9
txTs=2
colorCode=0
[USRP]
address=127.0.0.1
txPort={p['usrp_tx']}
rxPort={p['usrp_rx']}
usrpAudio=AUDIO_USE_GAIN
usrpGain=1.00
usrpAGC=-20,10,100
tlvAudio=AUDIO_UNITY
tlvGain=0.35
[DV3000]
address=127.0.0.1
rxPort={p['vocoder']}
"""
def mosquitto_config(plan):
 return f"""listener {plan['ports']['mqtt']} 127.0.0.1
allow_anonymous true
persistence false
"""
def dvswitch_config(plan):
 p=plan["ports"]
 return f"""[DSTAR]
address=127.0.0.1
txPort={p['ambe_rx']}
rxPort={p['ambe_tx']}
fallbackID={plan['settings']['dmr_id']}
exportTG=9
slot=2
RemotePort=0
"""
def integration_files(root,plan):
 cp=safe_regular(root,CONFIG); rp=safe_regular(root,RPT); mp=safe_regular(root,MODULES); cfg=json.loads(cp.read_text()); s=plan["settings"]; node=plan["bridgeNode"]; p=plan["ports"]
 entry={"id":s["bridge_id"],"mode":"dstar","node":str(node),"title":s["title"],"cardType":"standard","backendMode":"managed","bridgePermission":"self_owned","clientSource":"dstar","instance":s["bridge_id"],"setupPorts":p,"dstarReflector":s["reflector"],"dstarModule":s["module"],"liveAudioVerified":False}
 bs=cfg.setdefault("bridges",[]); ix=[i for i,b in enumerate(bs) if isinstance(b,dict) and b.get("id")==s["bridge_id"]]
 if len(ix)>1: raise PlanError("duplicate bridge identity")
 if ix: bs[ix[0]]=entry
 else: bs.append(entry)
 m=load("asr_m17_dstar","asr-bridge-setup-m17.py"); rpt=m.insert_nodes_mapping(rp.read_text(),node); rpt=m.upsert_node_section(rpt,s["bridge_id"],node,p["usrp_rx"],p["usrp_tx"]).replace(f"[{node}] ; M17 Bridge ({s['bridge_id']})",f"[{node}] ; D-Star Bridge ({s['bridge_id']})"); modules=m.ensure_usrp_module(mp.read_text()).replace("ASR M17 bridge","ASR managed bridge")
 base=plan["resources"]["configDir"]
 return {CONFIG:((json.dumps(cfg,indent=2,sort_keys=True)+"\n").encode(),cp.stat().st_mode&0o777),RPT:(rpt.encode(),rp.stat().st_mode&0o777),MODULES:(modules.encode(),mp.stat().st_mode&0o777),f"{base}/ircddbgateway":(irc_config(plan).encode(),0o644),f"{base}/mosquitto.conf":(mosquitto_config(plan).encode(),0o644),f"{base}/MMDVM_Bridge.ini":(mmdvm_config(plan).encode(),0o644),f"{base}/Analog_Bridge.ini":(analog_config(plan).encode(),0o644),f"{base}/DVSwitch.ini":(dvswitch_config(plan).encode(),0o644)}
