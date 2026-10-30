# -*- coding: utf-8 -*-
"""
本地图片批处理工具
==================
在浏览器中批量处理图片：
  - Real-ESRGAN 超分放大（2x / 3x / 4x）
  - 按宽/高缩放
  - 输出宽度不超过 1000px 时锐化
  - 导出 JPG / PNG / WebP

所有图片均在本机处理，不会上传到云端。
依赖：Python 3.12+、Pillow；AI 超分需要 Real-ESRGAN ncnn-vulkan 可执行文件与模型。
启动：python server.py（默认 http://127.0.0.1:8765）
"""
import atexit
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote

from PIL import Image, ImageFilter

Image.MAX_IMAGE_PIXELS = 300_000_000  # 限制单图像素，避免超大图片耗尽内存

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(ROOT, 'static')
CONFIG_PATH = os.path.join(ROOT, 'config.json')


def _mark_hidden(path: str):
    """Windows 下把目录设为隐藏+系统，资源管理器默认不显示。"""
    if os.name != 'nt':
        return
    try:
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(path)
        if attrs != -1:
            ctypes.windll.kernel32.SetFileAttributesW(path, attrs | 0x2 | 0x4)
    except Exception:  # noqa: BLE001
        pass


def _prepare_work_root() -> str:
    """优先使用项目外临时目录；受限环境下回退到项目内隐藏缓存。"""
    candidates = []
    seen = set()
    home = os.path.expanduser('~')
    for p in (tempfile.gettempdir(),
              os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Temp'),
              os.path.join(home, 'AppData', 'Local', 'Temp'),
              os.path.join(home, '.imgbatch-work-root')):
        if not p:
            continue
        p = os.path.abspath(p)
        if p in seen or p == ROOT or p.startswith(ROOT + os.sep):
            continue
        seen.add(p)
        candidates.append(p)
    errors = []
    for base_dir in candidates:
        base = os.path.join(base_dir, f'imgbatch-work-{os.getpid()}')
        try:
            os.makedirs(base, exist_ok=True)
            for old in glob.glob(os.path.join(base_dir, 'imgbatch-work*')):
                if old != base:
                    shutil.rmtree(old, ignore_errors=True)
            probe = os.path.join(base, '.write_test')
            with open(probe, 'wb') as fp:
                fp.write(b'1')
            os.remove(probe)
            return base
        except OSError as e:
            errors.append(f'{base}: {e}')
            continue
    # 受限环境兜底：项目外不可写时，使用项目内隐藏缓存（启动清理，退出删除）。
    base = os.path.join(ROOT, f'.imgbatch-cache-{os.getpid()}')
    try:
        for old in glob.glob(os.path.join(ROOT, '.imgbatch-cache-*')) + \
                glob.glob(os.path.join(ROOT, '.imgbatch-work*')):
            if old != base:
                shutil.rmtree(old, ignore_errors=True)
        os.makedirs(base, exist_ok=True)
        _mark_hidden(base)
        probe = os.path.join(base, '.write_test')
        with open(probe, 'wb') as fp:
            fp.write(b'1')
        os.remove(probe)
        return base
    except OSError as e:
        errors.append(f'{base}: {e}')
    raise RuntimeError('无法创建临时工作目录，已尝试：' + '；'.join(errors))


WORK_ROOT = _prepare_work_root()
UPLOAD_ROOT = os.path.join(WORK_ROOT, 'uploads')
OUTPUT_ROOT = os.path.join(WORK_ROOT, 'output')
os.makedirs(UPLOAD_ROOT, exist_ok=True)
os.makedirs(OUTPUT_ROOT, exist_ok=True)
atexit.register(shutil.rmtree, WORK_ROOT, ignore_errors=True)

DEFAULT_CONFIG = {
    "exe": os.path.join(ROOT, 'RealESRGAN', 'realesrgan-ncnn-vulkan.exe'),
    "model_dir": os.path.join(ROOT, 'RealESRGAN', 'models'),
    "model": "realesrgan-x4plus-anime",
    "tile": 0,          # 0 = 自动
    "gpu": "auto",
    "port": 8765,
    "engine_timeout": 1800,  # 单张超分子进程超时（秒），0 = 不限制
}

CONFIG_LOCK = threading.Lock()

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.gif'}
RE_INPUT_EXTS = {'.png', '.jpg', '.jpeg', '.webp'}  # exe 原生支持的输入格式
SHARPEN_MAX_WIDTH = 1000  # 锐化只对输出宽度不超过该值的图片生效
MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 单张上传大小上限（字节）

CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    else:
        save_config(cfg)
    for key in ('exe', 'model_dir'):
        value = cfg.get(key)
        if isinstance(value, str) and value and not os.path.isabs(value):
            cfg[key] = os.path.normpath(os.path.join(ROOT, value))
    return cfg


def save_config(cfg):
    """原子写入配置；项目内路径以相对路径保存，便于迁移。"""
    out = dict(cfg)
    for key in ('exe', 'model_dir'):
        value = out.get(key)
        if isinstance(value, str) and value:
            try:
                rel = os.path.relpath(value, ROOT)
            except ValueError:
                rel = value
            if not rel.startswith('..'):
                out[key] = rel
    tmp = CONFIG_PATH + '.tmp'
    with CONFIG_LOCK:
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_PATH)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


CONFIG = load_config()


def model_files_ok(exe_dir: str, model: str) -> bool:
    mdir = os.path.join(exe_dir, 'models')
    return all(os.path.isfile(os.path.join(mdir, f'{model}.{e}')) for e in ('param', 'bin'))


def resolve_engine():
    """探测可用的 Real-ESRGAN 引擎：优先项目内 RealESRGAN/，其次 config 指定目录。"""
    model = CONFIG['model']
    bases = [os.path.join(ROOT, 'RealESRGAN')]
    configured_dir = CONFIG.get('model_dir') or ''
    if configured_dir:
        bases.append(os.path.dirname(os.path.abspath(configured_dir)))
    seen = set()
    for base in bases:
        if not base or base in seen:
            continue
        seen.add(base)
        exe = os.path.join(base, 'realesrgan-ncnn-vulkan.exe')
        if os.path.isfile(exe) and model_files_ok(base, model):
            new_dir = os.path.join(base, 'models')
            if CONFIG['exe'] != exe or CONFIG['model_dir'] != new_dir:
                CONFIG['exe'] = exe
                CONFIG['model_dir'] = new_dir
                save_config(CONFIG)
            return True
    return False

# 单进程本地运行，会话与任务状态保存在内存中
SOURCES = {}   # sid -> {'files': [{'abs','rel','name'}], 'label': str}
JOBS = {}      # job_id -> dict
JOBS_LOCK = threading.Lock()
ACTIVE_JOB = None


# ---------------------------------------------------------------- 工具函数
def sanitize_rel(rel: str) -> str:
    """清理相对路径，防目录穿越；保留子文件夹结构。"""
    if not rel:
        return ''
    rel = rel.replace('\\', '/')
    parts = [p for p in rel.split('/') if p not in ('', '.', '..')]
    return '/'.join(parts)


def safe_name(name: str) -> str:
    name = os.path.basename(name.replace('\\', '/'))
    name = re.sub(r'[<>:"|?*\x00-\x1f]', '_', name).strip()
    return name or 'image'


def scan_images(root: str):
    """递归收集图片文件，返回 [{'abs','rel','name'}]，结构镜像。"""
    out = []
    if os.path.isfile(root):
        if os.path.splitext(root)[1].lower() in IMAGE_EXTS:
            out.append({'abs': root, 'rel': '', 'name': os.path.basename(root)})
        return out
    for cur, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if os.path.splitext(fn)[1].lower() not in IMAGE_EXTS:
                continue
            p = os.path.join(cur, fn)
            rel = os.path.relpath(cur, root).replace('\\', '/')
            if rel == '.':
                rel = ''
            out.append({'abs': p, 'rel': rel, 'name': fn})
    out.sort(key=lambda x: (x['rel'], x['name']))
    return out


# ---------------------------------------------------------------- 图片处理管线
def re_upscale(src: str, dst_png: str, progress_cb=None):
    """调用 realesrgan-ncnn-vulkan.exe，固定 -s 4。"""
    cmd = [
        CONFIG['exe'],
        '-i', src,
        '-o', dst_png,
        '-s', '4',
        '-t', str(CONFIG.get('tile', 0)),
        '-n', CONFIG['model'],
        '-g', str(CONFIG.get('gpu', 'auto')),
    ]
    creation = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            universal_newlines=True, errors='replace', creationflags=creation)

    def pump():
        try:
            for line in proc.stderr:
                if progress_cb:
                    m = re.search(r'(\d+[.,]\d+)%', line)
                    if m:
                        try:
                            progress_cb(float(m.group(1).replace(',', '.')))
                        except ValueError:
                            pass
        except (ValueError, OSError):
            pass

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timeout = int(CONFIG.get('engine_timeout', 1800) or 0)
    try:
        rc = proc.wait(timeout=timeout or None)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise RuntimeError(f'超分引擎超过 {timeout} 秒未完成，已终止')
    finally:
        reader.join(timeout=5)
    if rc:
        raise RuntimeError(f'超分引擎退出码 {rc}（请检查 GPU/Vulkan 与模型文件）')


def pil_open(src: str) -> Image.Image:
    """打开图片；GIF 取第一帧；P 模式转 RGBA。"""
    im = Image.open(src)
    if getattr(im, 'is_animated', False):
        im.seek(0)
    if im.mode == 'P':
        im = im.convert('RGBA')
    im.load()
    return im


def compute_target(w: int, h: int, opts: dict):
    """返回 (目标宽, 目标高)：指定宽时按比例算高，指定高时按比例算宽。"""
    mode = opts['scaleMode']
    if mode in ('x2', 'x3', 'x4'):
        f = int(mode[1])
        return w * f, h * f
    if mode == 'width':
        tw = int(opts['scaleValue'])
        return tw, max(1, round(tw * h / w))
    if mode == 'height':
        th = int(opts['scaleValue'])
        return max(1, round(th * w / h)), th
    return w, h


def sharpen_image(img: Image.Image, strength: int) -> Image.Image:
    """3×3 反锐化卷积：k = 强度 / 100，核 [[0,-k,0],[-k,1+4k,-k],[0,-k,0]]。"""
    k = max(0.0, min(1.0, strength / 100.0))
    if k <= 0:
        return img
    kernel = (0.0, -k, 0.0,
              -k, 1.0 + 4.0 * k, -k,
              0.0, -k, 0.0)
    return img.filter(ImageFilter.Kernel((3, 3), kernel, scale=1, offset=0))


def save_output(img: Image.Image, out_path: str, fmt: str, quality: int):
    fmt = fmt.lower()
    if fmt in ('jpg', 'jpeg'):
        if img.mode != 'RGB':
            rgba = img.convert('RGBA')
            bg = Image.new('RGB', rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        img.save(out_path, 'JPEG', quality=quality, optimize=True, progressive=True)
    elif fmt == 'png':
        img.save(out_path, 'PNG', optimize=True)
    else:  # webp
        img.save(out_path, 'WEBP', quality=quality, method=6)


def new_temp_png() -> str:
    """创建安全的临时 PNG 路径（mkstemp 后立即关闭句柄，供外部程序写入）。"""
    fd, path = tempfile.mkstemp(suffix='.png')
    os.close(fd)
    return path


def unique_out_path(out_dir: str, out_name: str) -> str:
    """输出重名时追加 -2、-3…，避免同名不同扩展名的源图互相覆盖。"""
    path = os.path.join(out_dir, out_name)
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(out_name)
    i = 2
    while True:
        cand = os.path.join(out_dir, f'{stem}-{i}{ext}')
        if not os.path.exists(cand):
            return cand
        i += 1


def process_one(entry: dict, job: dict):
    """处理单张图片（完整管线）。"""
    opts = job['options']
    src = entry['abs']
    f = entry  # 结果写回 entry
    f['status'] = 'processing'
    f['note'] = ''
    f['sharpen_skipped'] = False
    t0 = time.time()

    tmp_files = []
    img = None
    try:
        with pil_open(src) as im:
            w, h = im.size
            f['src_size'] = f'{w}x{h}'
            target_w, target_h = compute_target(w, h, opts)

            mode = opts['scaleMode']
            img = im
            if mode in ('x2', 'x3', 'x4'):
                # AI 超分固定 4 倍模型；2x/3x 先放大到 4 倍，再用 Lanczos 降采样到目标尺寸
                f['note'] = 'AI超分中'
                re_in = src
                if os.path.splitext(src)[1].lower() not in RE_INPUT_EXTS or im.mode not in ('RGB', 'RGBA'):
                    t = new_temp_png()
                    tmp_files.append(t)
                    conv = im.convert('RGBA') if im.mode not in ('RGB', 'RGBA') else im
                    conv.save(t, 'PNG')
                    re_in = t
                re_out = new_temp_png()
                tmp_files.append(re_out)
                re_upscale(re_in, re_out, progress_cb=lambda p: f.update(note=f'AI超分中 {p:.0f}%'))
                img = Image.open(re_out)
                if img.size != (target_w, target_h):
                    f['note'] = '降采样对齐目标尺寸'
                    img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                f['note'] = 'AI超分完成'
            elif mode in ('width', 'height'):
                img = im.resize((target_w, target_h), Image.Resampling.LANCZOS)
                f['note'] = '缩放完成'

            # 锐化：仅当处理后宽度不超过 SHARPEN_MAX_WIDTH
            sharpened = False
            if opts.get('sharpen'):
                if img.width <= SHARPEN_MAX_WIDTH:
                    img = sharpen_image(img, int(opts.get('sharpenStrength', 50)))
                    sharpened = True
                else:
                    f['sharpen_skipped'] = True

            # 输出文件名：原名 (x2|w800|h600|原图)[ 锐化].扩展名
            fmt = opts['format'].lower()
            stem = os.path.splitext(entry['name'])[0]
            if mode == 'none':
                mode_tag = '原图'
            elif mode in ('x2', 'x3', 'x4'):
                mode_tag = mode
            else:
                mode_tag = f"{mode[0]}{opts['scaleValue']}"  # w800 / h600
            tag = f'{mode_tag} 锐化' if sharpened else mode_tag
            out_name = safe_name(f'{stem} ({tag}).{fmt}')
            rel_dir = entry.get('rel', '')
            out_dir = os.path.join(job['out_dir'], rel_dir) if rel_dir else job['out_dir']
            os.makedirs(out_dir, exist_ok=True)
            out_path = unique_out_path(out_dir, out_name)
            out_name = os.path.basename(out_path)
            save_output(img, out_path, fmt, int(opts.get('quality', 80)))

            f.update({
                'status': 'done',
                'out': out_path,
                'out_rel': os.path.relpath(out_path, job['out_dir']).replace('\\', '/'),
                'out_name': out_name,
                'out_size': f'{img.width}x{img.height}',
                'bytes': os.path.getsize(out_path),
                'note': f"{f.get('src_size','')} → {img.width}x{img.height}"
                        + ('，已锐化' if sharpened else '')
                        + (f'，宽度>{SHARPEN_MAX_WIDTH}px跳过锐化' if f.get('sharpen_skipped') else ''),
                'sharpen_applied': sharpened,
            })
    finally:
        if img is not None:
            try:
                img.close()
            except Exception:  # noqa: BLE001
                pass
        for t in tmp_files:
            if t and os.path.exists(t):
                try:
                    os.remove(t)
                except OSError:
                    pass
    f['ms'] = int((time.time() - t0) * 1000)


def run_job(job_id: str):
    global ACTIVE_JOB
    job = JOBS[job_id]
    job['state'] = 'running'
    ok = err = 0
    try:
        for i, entry in enumerate(job['files']):
            if job.get('cancel'):
                entry['status'] = 'cancelled'
                continue
            try:
                process_one(entry, job)
                ok += 1
            except Exception as e:  # noqa: BLE001 —— 单张失败不中断批处理
                entry['status'] = 'error'
                entry['note'] = f'{type(e).__name__}: {e}'
                err += 1
            job['done'] = i + 1
            job['ok'] = ok
            job['err'] = err
    finally:
        job['ok'] = ok
        job['err'] = err
        job['state'] = 'done'
        job['finished_at'] = time.time()
        with JOBS_LOCK:
            if ACTIVE_JOB == job_id:
                ACTIVE_JOB = None


# ---------------------------------------------------------------- HTTP 服务
class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ImgBatch/1.0'

    # ---- 基础 ----
    def log_message(self, fmt, *args):  # 安静模式
        pass

    def send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: str, mime: str, download_name=None):
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            self.send_json({'error': 'file not found'}, 404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        if download_name:
            ext = os.path.splitext(download_name)[1].lower() or '.bin'
            ascii_name = 'image' + ext
            self.send_header('Content-Disposition',
                             f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(download_name)}")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self) -> bytes:
        length = int(self.headers.get('Content-Length') or 0)
        remaining = length
        chunks = []
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)

    # ---- 路由 ----
    def do_GET(self):
        try:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            route = unquote(u.path)
            if route in ('/', '/index.html'):
                self.send_file(os.path.join(STATIC_DIR, 'index.html'), 'text/html; charset=utf-8')
            elif route == '/app.js':
                self.send_file(os.path.join(STATIC_DIR, 'app.js'), 'text/javascript; charset=utf-8')
            elif route == '/style.css':
                self.send_file(os.path.join(STATIC_DIR, 'style.css'), 'text/css; charset=utf-8')
            elif route == '/favicon.ico':
                self.send_json({'ok': True})
            elif route == '/api/ping':
                self.api_ping()
            elif route == '/api/status':
                self.api_status(q.get('job', [''])[0])
            elif route == '/api/zip':
                self.api_zip(q.get('job', [''])[0])
            elif route == '/api/preview':
                self.api_preview(q.get('job', [''])[0], q.get('i', ['0'])[0])
            else:
                self.send_json({'error': 'not found'}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self.send_json({'error': str(e)}, 500)
            except Exception:  # noqa: BLE001
                pass

    def do_POST(self):
        try:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            route = unquote(u.path)
            if route == '/api/scan':
                self.api_scan()
            elif route == '/api/upload':
                self.api_upload(q)
            elif route == '/api/process':
                self.api_process()
            elif route == '/api/open':
                self.api_open()
            else:
                self.send_json({'error': 'not found'}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self.send_json({'error': str(e)}, 500)
            except Exception:  # noqa: BLE001
                pass

    # ---- API ----
    def api_ping(self):
        exe_ok = os.path.isfile(CONFIG['exe'])
        model_ok = all(os.path.isfile(os.path.join(CONFIG['model_dir'], f'{CONFIG["model"]}.{ext}'))
                       for ext in ('param', 'bin'))
        pil_ver = getattr(Image, '__version__', '?')
        self.send_json({
            'ok': True, 'app': '图片批处理工具箱',
            'exe_found': exe_ok,
            'model_found': model_ok, 'model': CONFIG['model'],
            'pillow': pil_ver,
            'ready': exe_ok and model_ok,
            'download_dir': _downloads_dir(),
        })

    def read_json(self):
        """解析 JSON 请求体；兼容 UTF-8（浏览器）与 GBK（部分本地客户端）。"""
        body = self.read_body()
        if not body:
            return {}
        for enc in ('utf-8', 'gbk'):
            try:
                return json.loads(body.decode(enc))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        raise ValueError('请求体不是有效的 JSON')

    def api_scan(self):
        """扫描本地路径（文件或文件夹），注册为处理源。"""
        body = self.read_json()
        path = os.path.normpath(body.get('path', '')).strip()
        if not path or not os.path.exists(path):
            self.send_json({'error': '路径不存在'}, 400)
            return
        files = scan_images(path)
        if not files:
            self.send_json({'error': '该路径下没有找到图片文件'}, 400)
            return
        sid = uuid.uuid4().hex[:12]
        SOURCES[sid] = {'files': files, 'label': path}
        self.send_json({
            'sid': sid, 'count': len(files),
            'label': path,
            'sample': [f'{f["rel"] + "/" if f["rel"] else ""}{f["name"]}' for f in files[:8]],
        })

    def api_upload(self, q):
        """原始字节流上传单张图片：/api/upload?sid=&name=&rel="""
        sid = (q.get('sid', [''])[0] or '').strip()
        name = safe_name(unquote(q.get('name', [''])[0]))
        rel = sanitize_rel(unquote(q.get('rel', [''])[0]))
        if not sid or not name:
            self.send_json({'error': '缺少 sid 或文件名'}, 400)
            return
        if os.path.splitext(name)[1].lower() not in IMAGE_EXTS:
            self.send_json({'error': f'不支持的格式: {name}'}, 400)
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self.send_json({'error': '上传内容为空'}, 400)
            return
        if length > MAX_UPLOAD_BYTES:
            self.send_json({'error': f'单张图片不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB'}, 413)
            return
        folder = os.path.join(UPLOAD_ROOT, sid, rel) if rel else os.path.join(UPLOAD_ROOT, sid)
        path = os.path.join(folder, name)
        body = self.read_body()
        if len(body) != length:
            self.send_json({'error': '上传数据不完整'}, 400)
            return
        os.makedirs(folder, exist_ok=True)
        with open(path, 'wb') as fp:
            fp.write(body)
        files = SOURCES.setdefault(sid, {'files': [], 'label': f'上传会话 {sid}'})

        # 同一 sid 下同名文件覆盖，其他文件保留
        entry = {'abs': path, 'rel': rel, 'name': name}
        files['files'] = [e for e in files['files'] if not (e['abs'] == path)] + [entry]
        self.send_json({'ok': True, 'sid': sid, 'count': len(files['files'])})

    def api_process(self):
        global ACTIVE_JOB
        body = self.read_json()
        sid = body.get('sid', '')
        opts = body.get('options', {}) or {}
        source = SOURCES.get(sid)
        if not source or not source['files']:
            self.send_json({'error': '没有可处理的图片，请先选择图片或填写路径'}, 400)
            return
        # 参数校验
        mode = opts.get('scaleMode', 'none')
        if mode not in ('none', 'x2', 'x3', 'x4', 'width', 'height'):
            self.send_json({'error': f'未知尺寸模式 {mode}'}, 400)
            return
        if mode in ('width', 'height'):
            v = opts.get('scaleValue')
            if not isinstance(v, int) or not (1 <= v <= 20000):
                self.send_json({'error': '请填写 1~20000 之间的宽度/高度'}, 400)
                return
        fmt = (opts.get('format') or 'webp').lower()
        if fmt not in ('jpg', 'jpeg', 'png', 'webp'):
            self.send_json({'error': f'不支持的输出格式 {fmt}'}, 400)
            return
        opts['format'] = fmt
        opts['quality'] = max(1, min(100, int(opts.get('quality', 80))))
        opts['sharpen'] = bool(opts.get('sharpen'))
        opts['sharpenStrength'] = max(0, min(100, int(opts.get('sharpenStrength', 50))))
        if mode in ('x2', 'x3', 'x4') and not (os.path.isfile(CONFIG['exe'])
                and all(os.path.isfile(os.path.join(CONFIG['model_dir'], f'{CONFIG["model"]}.{e}')) for e in ('param', 'bin'))):
            self.send_json({'error': '未找到超分引擎或模型，请检查 config.json 配置'}, 500)
            return

        job_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4]
        out_dir = os.path.join(OUTPUT_ROOT, job_id)
        files = []
        for e in source['files']:
            files.append({'abs': e['abs'], 'rel': e['rel'], 'name': e['name'], 'status': 'pending'})
        job = {
            'id': job_id, 'options': opts, 'out_dir': out_dir,
            'files': files, 'total': len(files), 'done': 0, 'ok': 0, 'err': 0,
            'state': 'queued', 'created_at': time.time(),
            'label': source.get('label', ''),
        }
        with JOBS_LOCK:
            if ACTIVE_JOB:
                self.send_json({'error': '已有任务在处理中，请等待完成'}, 409)
                return
            os.makedirs(out_dir, exist_ok=True)
            JOBS[job_id] = job
            ACTIVE_JOB = job_id
        threading.Thread(target=run_job, args=(job_id,), daemon=True).start()
        self.send_json({'job': job_id, 'total': len(files), 'out_dir': out_dir})

    def api_status(self, job_id: str):
        job = JOBS.get(job_id)
        if not job:
            self.send_json({'error': '任务不存在'}, 404)
            return
        self.send_json({
            'id': job['id'], 'state': job['state'],
            'total': job['total'], 'done': job['done'],
            'ok': job['ok'], 'err': job['err'],
            'out_dir': job['out_dir'],
            'files': [{k: f.get(k) for k in
                       ('name', 'rel', 'status', 'note', 'out_name', 'out_rel', 'out_size',
                        'bytes', 'ms', 'sharpen_applied', 'sharpen_skipped')}
                      for f in job['files']],
        })

    def api_zip(self, job_id: str):
        job = JOBS.get(job_id)
        if not job:
            self.send_json({'error': '任务不存在'}, 404)
            return
        if job['state'] != 'done':
            self.send_json({'error': '任务尚未完成'}, 409)
            return
        zip_path = job['out_dir'] + '.zip'
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for cur, _d, fs in os.walk(job['out_dir']):
                    for fn in fs:
                        full = os.path.join(cur, fn)
                        arc = os.path.relpath(full, job['out_dir'])
                        zf.write(full, arc)
            size = os.path.getsize(zip_path)
            fname = f'处理结果-{job_id}.zip'
            ascii_name = f'result-{job_id}.zip'
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Length', str(size))
            self.send_header('Content-Disposition',
                             f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(fname)}")
            self.end_headers()
            with open(zip_path, 'rb') as f:
                shutil.copyfileobj(f, self.wfile)
        finally:
            try:
                os.remove(zip_path)
            except OSError:
                pass

    def api_preview(self, job_id: str, idx: str):
        """预览结果图（缩略图级，直接返回原文件——本地应用无需缩略）。"""
        job = JOBS.get(job_id)
        if not job:
            self.send_json({'error': '任务不存在'}, 404)
            return
        try:
            f = job['files'][int(idx)]
        except (ValueError, IndexError):
            self.send_json({'error': '索引错误'}, 400)
            return
        if f.get('status') != 'done' or not f.get('out') or not os.path.isfile(f['out']):
            self.send_json({'error': '结果不存在'}, 404)
            return
        ext = os.path.splitext(f['out'])[1].lower()
        mime = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                '.webp': 'image/webp'}.get(ext, 'application/octet-stream')
        self.send_file(f['out'], mime, f.get('out_name') or os.path.basename(f['out']))

    def api_open(self):
        """打开下载文件夹（浏览器保存图片的默认位置）。"""
        body = self.read_json()
        path = (body.get('path') or '').strip()
        target = path if (path and os.path.isdir(path)) else _downloads_dir()
        if not os.path.isdir(target):
            self.send_json({'error': '下载文件夹不存在', 'path': target}, 400)
            return
        try:
            _open_folder(target)
        except OSError as e:
            self.send_json({'error': f'打开文件夹失败: {e}（路径：{target}）', 'path': target}, 500)
            return
        self.send_json({'ok': True, 'path': target})


def _downloads_dir() -> str:
    """返回浏览器/系统默认下载目录；优先环境变量，其次 Edge/Chrome 配置与注册表。"""
    override = (os.environ.get('IMGTOOL_DOWNLOAD_DIR') or '').strip()
    if override and os.path.isdir(override):
        return override
    home = os.path.expanduser('~')
    local = os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
    candidates = []
    for rel in (r'Microsoft\Edge\User Data\Default\Preferences',
                r'Google\Chrome\User Data\Default\Preferences'):
        pref = os.path.join(local, rel)
        try:
            with open(pref, encoding='utf-8') as fp:
                data = json.load(fp)
            path = (data.get('download') or {}).get('default_directory')
            if isinstance(path, str) and path.strip():
                path = os.path.expandvars(os.path.expanduser(path))
                if os.path.isdir(path):
                    candidates.append(path)
        except (OSError, ValueError):
            pass
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                value, _ = winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')
            path = os.path.expandvars(value)
            if os.path.isdir(path):
                candidates.append(path)
        except (OSError, ImportError):
            pass
    for path in (os.path.join(home, 'Downloads'), os.path.join(home, 'OneDrive', 'Downloads'), home):
        if os.path.isdir(path):
            candidates.append(path)
    for path in candidates:
        if os.path.isdir(path):
            return path
    return home


def _open_folder(target: str):
    """用系统文件管理器打开目录；失败时由上层返回明确错误，不再启动 explorer。"""
    if os.name != 'nt':
        subprocess.Popen(['xdg-open', target])
        return
    os.startfile(target)  # noqa: S606


def find_free_port(start: int) -> int:
    import socket
    for p in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', p))
                return p
            except OSError:
                continue
    raise RuntimeError('无可用端口')


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass
    port = find_free_port(int(CONFIG.get('port', 8765)))
    os.makedirs(UPLOAD_ROOT, exist_ok=True)
    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    engine_ok = resolve_engine()
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    url = f'http://127.0.0.1:{port}'
    print(f'✅ 图片批处理工具箱已启动: {url}', flush=True)
    print(f'   超分引擎: {CONFIG["exe"]} [{"OK" if engine_ok else "未找到（AI放大不可用）"}]', flush=True)
    print(f'   模型: {CONFIG["model"]} @ {CONFIG["model_dir"]}', flush=True)
    print('   按 Ctrl+C 停止服务', flush=True)
    if os.environ.get('IMGTOOL_NO_BROWSER') != '1':
        threading.Timer(0.8, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n服务已停止')


if __name__ == '__main__':
    main()
