#!/usr/bin/env python3
"""Compare screenshot baseline; tolerate small dynamic text/antialias changes."""
import sys,pathlib,json
from PIL import Image,ImageChops
baseline=pathlib.Path(sys.argv[1]);candidate=pathlib.Path(sys.argv[2]);results=[]
for original in sorted(baseline.glob('*.png')):
    current=candidate/original.name
    if not current.exists():
        results.append({'image':original.name,'error':'missing'});continue
    with Image.open(original) as a,Image.open(current) as b:
        if a.size!=b.size:
            results.append({'image':original.name,'error':f'size {a.size} vs {b.size}'});continue
        diff=ImageChops.difference(a.convert('RGB'),b.convert('RGB'))
        changed=diff.convert('L').point(lambda x:255 if x>8 else 0).histogram()[255]
        percent=100*changed/(a.width*a.height)
        if percent>0.25:results.append({'image':original.name,'changed_percent':round(percent,4),'error':'above 0.25% threshold'})
if len(list(candidate.glob('*.png')))!=len(list(baseline.glob('*.png'))):results.append({'error':'image count differs'})
print(json.dumps({'baseline_count':len(list(baseline.glob('*.png'))),'threshold_percent':0.25,'failures':results},indent=2))
if results:raise SystemExit(1)
