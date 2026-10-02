#!/usr/bin/env python3
import argparse, importlib.util, json, os, re, stat, sys, tempfile
from pathlib import Path
def config_path():
    if not os.environ.get('ASR_CONTAINER_PROVISIONING_PROFILE'):return Path('/etc/allscan-reimagined/config.json')
    source=Path(__file__).resolve().parent/'asr-provisioning-backend.py'
    if not source.is_file():source=Path('/usr/local/libexec/allscan-reimagined/asr-provisioning-backend.py')
    spec=importlib.util.spec_from_file_location('asr_backend_dmr_control',source)
    if not spec or not spec.loader:raise SystemExit('container provisioning backend unavailable')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module.map_path(Path('/'),'/etc/allscan-reimagined/config.json')
CONFIG=config_path()
def runtime_root():
    if not os.environ.get('ASR_CONTAINER_PROVISIONING_PROFILE'):return Path('/opt/allscan-reimagined-bridges/urf')
    source=Path('/usr/local/libexec/allscan-reimagined/asr-provisioning-backend.py')
    spec=importlib.util.spec_from_file_location('asr_backend_dmr_runtime',source)
    if not spec or not spec.loader:raise SystemExit('container provisioning backend unavailable')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    return module.map_path(Path('/'),'/opt/allscan-reimagined-bridges/urf')
ROOT=runtime_root()
def bridge(bid):
    data=json.loads(CONFIG.read_text())
    for b in data.get('bridges',[]):
        if isinstance(b,dict) and b.get('id')==bid and b.get('cardType')=='dmr_net' and b.get('backendMode')=='managed':
            return b
    raise SystemExit('managed DMR Net Bridge not found')
def target_path(b):
    bid=str(b.get('id') or '')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,31}',bid): raise SystemExit('invalid bridge ID')
    logical=Path('/opt/allscan-reimagined-bridges/urf')/bid/'tgif-run'/'net-target'
    if Path(str(b.get('managedTargetFile') or logical)) != logical: raise SystemExit('invalid managed target path')
    expected=ROOT/bid/'tgif-run'/'net-target';p=expected
    for parent in (ROOT, ROOT/bid, p.parent):
        if parent.is_symlink(): raise SystemExit('unsafe managed target path')
    if p.is_symlink(): raise SystemExit('unsafe managed target file')
    if p.exists():
        info=p.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SystemExit('unsafe managed target file')
    return p
def write_target(p,tg):
    p.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.net-target.',dir=p.parent)
    try:
        os.write(fd,(str(tg)+'\n').encode()); os.fchmod(fd,0o644); os.close(fd); fd=-1
        os.replace(name,p)
    finally:
        if fd>=0: os.close(fd)
        try: os.unlink(name)
        except FileNotFoundError: pass
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bridge',required=True); g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--connect'); g.add_argument('--disconnect',action='store_true'); g.add_argument('--status',action='store_true')
    a=ap.parse_args(); b=bridge(a.bridge); p=target_path(b)
    if a.connect:
        if not re.fullmatch(r'\d{1,8}',a.connect): raise SystemExit('invalid talkgroup')
        tg=int(a.connect)
        if tg<1 or tg>16777215 or tg==4000: raise SystemExit('invalid talkgroup')
        write_target(p,tg)
    elif a.disconnect:
        try:p.unlink()
        except FileNotFoundError:pass
    tg=''
    if p.is_file():
        try: tg=str(int(p.read_text().strip()))
        except Exception: tg=''
    print(json.dumps({'ok':True,'bridgeId':a.bridge,'currentTg':tg,'linked':bool(tg)}))
if __name__=='__main__': main()
