# -*- coding: utf-8 -*-
"""端到端测试：覆盖超分/缩放/锐化/格式输出/上传/ZIP 全流程。

运行前需启动 server.py，并在 test_images/ 下准备测试图片。
端口不是 8765 时，设置 IMGTOOL_PORT 环境变量。
"""
import io
import json
import os
import sys
import time
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors='replace')
    except (AttributeError, ValueError):
        pass

BASE = f"http://127.0.0.1:{os.environ.get('IMGTOOL_PORT', '8765')}"
ROOT = os.path.dirname(os.path.abspath(__file__))


def api(path, body=None, raw=None):
    if raw is not None:
        req = urllib.request.Request(BASE + path, data=raw, method='POST')
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode('utf-8'))
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method='POST',
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode('utf-8'))


def wait_job(job, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with urllib.request.urlopen(f'{BASE}/api/status?job={job}') as r:
            d = json.loads(r.read().decode('utf-8'))
        if d['state'] == 'done':
            return d
        time.sleep(1)
    raise TimeoutError(job)


def check(name, cond, detail=''):
    mark = '✅' if cond else '❌'
    print(f'{mark} {name} {detail}')
    if not cond:
        FAILS.append(name)


FAILS = []
PIL_OK = True
try:
    from PIL import Image
except ImportError:
    PIL_OK = False

print('== 0. ping ==')
p = api.__globals__  # noqa
import urllib.request as _u
with _u.urlopen(BASE + '/api/ping') as r:
    ping = json.loads(r.read().decode('utf-8'))
check('引擎就绪', ping['ready'], f"exe={ping['exe_found']} model={ping['model_found']}")

print('== 1. 文件夹批处理：AI x2 → webp ==')
scan = api('/api/scan', {'path': os.path.join(ROOT, 'test_images')})
check('扫描数量=4', scan['count'] == 4, f"got {scan['count']}")
job = api('/api/process', {'sid': scan['sid'], 'options': {
    'scaleMode': 'x2', 'format': 'webp', 'quality': 85}})
st = wait_job(job['job'])
check('全部成功', st['err'] == 0 and st['ok'] == 4, f"ok={st['ok']} err={st['err']}")
for f in st['files']:
    print('   -', f['out_name'], f['note'])
# 验证尺寸
sizes = {f['out_name']: f['out_size'] for f in st['files'] if f['status'] == 'done'}
check('input.jpg x2=440x440', any(v == '440x440' for v in sizes.values()), str(sizes))
check('mid.jpg x2=1600x1000', any(v == '1600x1000' for v in sizes.values()))
check('alpha_small x2=600x400', any(v == '600x400' for v in sizes.values()))
check('子目录结构保留', any((f.get('out_rel') or '').startswith('sub') for f in st['files'] if f['status'] == 'done'),
      str([f.get('out_rel') for f in st['files']]))
OUT1 = st['out_dir']

print('== 2. 指定宽度=1000 + 锐化(应生效,宽<=1000) → jpg ==')
job = api('/api/process', {'sid': scan['sid'], 'options': {
    'scaleMode': 'width', 'scaleValue': 1000, 'sharpen': True,
    'sharpenStrength': 60, 'format': 'jpg', 'quality': 90}})
st = wait_job(job['job'])
check('全部成功', st['err'] == 0, f"err={st['err']}")
for f in st['files']:
    print('   -', f['out_name'], f['note'])
    check(f"锐化生效[{f['name']}]", f.get('sharpen_applied') is True, f.get('note', ''))
check('mid.jpg 宽1000→高625', any(f['out_size'] == '1000x625' for f in st['files'] if f['status'] == 'done'))
OUT2 = st['out_dir']

print('== 3. 指定宽度=2000 + 锐化(应跳过,宽>1000) ==')
job = api('/api/process', {'sid': scan['sid'], 'options': {
    'scaleMode': 'width', 'scaleValue': 2000, 'sharpen': True,
    'sharpenStrength': 60, 'format': 'png'}})
st = wait_job(job['job'])
for f in st['files']:
    print('   -', f['out_name'], f['note'])
    check(f"跳过锐化[{f['name']}]", f.get('sharpen_skipped') is True and f.get('sharpen_applied') is False, f.get('note', ''))
check('mid.jpg 宽2000→高1250', any(f['out_size'] == '2000x1250' for f in st['files'] if f['status'] == 'done'))

print('== 4. 原图直转 WebP（跳过放大缩放锐化） ==')
job = api('/api/process', {'sid': scan['sid'], 'options': {
    'scaleMode': 'none', 'format': 'webp', 'quality': 80}})
st = wait_job(job['job'])
check('全部成功', st['err'] == 0, f"err={st['err']}")
for f in st['files']:
    print('   -', f['out_name'], f['note'])
    if f['name'] == 'mid.jpg':
        check('mid 原尺寸直转', f['out_size'] == '800x500', f['out_size'])

print('== 5. 上传接口（模拟浏览器）x3 → jpeg ==')
ups = []
for fn in ('mid.jpg', 'alpha_small.png'):
    p = os.path.join(ROOT, 'test_images', fn)
    with open(p, 'rb') as fp:
        raw = fp.read()
    from urllib.parse import quote
    d = api(f'/api/upload?sid=t{int(time.time())}&name={quote(fn)}&rel=', raw=raw)
    ups.append(d['sid'])
job = api('/api/process', {'sid': ups[-1], 'options': {
    'scaleMode': 'x3', 'format': 'jpeg', 'quality': 90}})
st = wait_job(job['job'])
check('x3 全部成功', st['err'] == 0, f"ok={st['ok']}")
for f in st['files']:
    print('   -', f['out_name'], f['note'])
    if f['name'] == 'mid.jpg':
        check('mid x3=2400x1500', f['out_size'] == '2400x1500', f['out_size'])
    if f['name'] == 'alpha_small.png':
        check('alpha png→jpeg 正常', f['out_size'] == '900x600', f['out_size'])

print('== 6. ZIP 下载 ==')
with urllib.request.urlopen(f'{BASE}/api/zip?job={job["job"]}') as r:
    zdata = r.read()
    cdisp = r.headers.get('Content-Disposition') or ''
check('ZIP 有效', zdata[:2] == b'PK' and len(zdata) > 1000, f'{len(zdata)} bytes')
check('ZIP 文件名不含本地路径', 'filename*=UTF-8' in cdisp and 'C:' not in cdisp and '\\' not in cdisp, cdisp)

print('== 7. 输出文件落盘验证 ==')
n1 = sum(len(fs) for _, _, fs in os.walk(OUT1))
check('任务1输出文件=4', n1 == 4, f'got {n1}')

print()
if FAILS:
    print('❌ 失败项:', FAILS)
    sys.exit(1)
print('🎉 全部测试通过')
