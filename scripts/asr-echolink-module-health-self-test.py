#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import sys
import tempfile
spec = importlib.util.spec_from_file_location('health', Path(__file__).with_name('asr-echolink-module-health.py'))
mod = importlib.util.module_from_spec(spec); sys.modules[spec.name] = mod; spec.loader.exec_module(mod)
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); conf=root/'modules.conf'; binary=root/'chan_echolink.so'; ast=root/'asterisk'
    ast.write_text('#!/bin/sh\necho "chan_echolink.so  Echolink Channel Driver  0  Running  extended"\n'); ast.chmod(0o755)
    conf.write_text('[modules]\nnoload = chan_echolink.so\n'); binary.touch()
    assert mod.check(conf,binary,str(ast))['configState']=='disabled'
    assert not mod.check(conf,binary,str(ast))['operational']
    conf.write_text('[modules]\nload = chan_echolink.so\n')
    assert mod.check(conf,binary,str(ast))['operational']
    binary.unlink()
    assert not mod.check(conf,binary,str(ast))['operational']
    binary.touch(); ast.write_text('#!/bin/sh\necho "0 modules loaded"\n')
    assert not mod.check(conf,binary,str(ast))['loaded']
    conf.write_text('[modules]\n; noload = chan_echolink.so\n')
    assert mod.config_state(conf.read_text()) == 'automatic-or-unspecified'
print('EchoLink module readiness: PASS')
