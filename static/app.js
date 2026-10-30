/* 图片批处理工具箱 - 前端逻辑 */
'use strict';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  srcMode: 'browser',     // browser | path
  sid: null,              // 上传会话/扫描会话 ID
  pendingFiles: [],       // 待上传 File 列表 [{file, rel}]
  uploaded: 0,
  ready: false,           // 是否已注册处理源（sid 有效）
  fileCount: 0,
  scaleMode: 'none',
  format: 'webp',
  job: null,
  pollTimer: null,
};

/* ---------------- 工具 ---------------- */
function toast(msg, isErr = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove('show'), 2600);
}

function fmtSize(n) {
  if (n == null) return '';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(2) + ' MB';
}

async function api(path, body) {
  const opt = body ? { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } } : {};
  const res = await fetch(path, opt);
  const data = await res.json().catch(() => ({ error: '响应解析失败' }));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

/* ---------------- 引擎状态 ---------------- */
async function ping() {
  try {
    const d = await api('/api/ping');
    state.downloadDir = d.download_dir || '';
    const dot = $('#engineDot'), txt = $('#engineText');
    if (d.ready) {
      dot.className = 'dot ok';
      txt.textContent = `超分引擎就绪（${d.model}）`;
    } else {
      dot.className = 'dot bad';
      txt.textContent = d.exe_found ? '模型缺失' : '超分引擎未找到（AI放大不可用）';
    }
  } catch {
    $('#engineText').textContent = '服务连接失败';
  }
}

/* ---------------- 步骤高亮 ---------------- */
function updateSteps() {
  $('#step1').classList.toggle('active', !state.ready);
  $('#step2').classList.toggle('active', state.ready);
  $('#step3').classList.toggle('active', state.ready);
  const btn = $('#btnStart'), hint = $('#startHint');
  btn.disabled = !state.ready;
  if (state.ready) {
    hint.textContent = `已就绪：${state.fileCount} 张图片 · 输出 ${state.format.toUpperCase()}`
      + (state.scaleMode !== 'none' ? ` · ${modeText(state.scaleMode)}` : '')
      + ($('#sharpenOn').checked ? ' · 锐化' : '');
  } else {
    hint.textContent = '请先选择图片';
  }
}

function modeText(m) {
  return { none: '不调整', x2: 'AI放大2倍', x3: 'AI放大3倍', x4: 'AI放大4倍',
           width: `宽${$('#scaleValue').value}px`, height: `高${$('#scaleValue').value}px` }[m] || m;
}

/* ---------------- 来源选择 ---------------- */
function setSrcMode(m) {
  state.srcMode = m;
  $('#srcBrowser').classList.toggle('on', m === 'browser');
  $('#srcPath').classList.toggle('on', m === 'path');
  $('#browserPane').classList.toggle('hidden', m !== 'browser');
  $('#pathPane').classList.toggle('hidden', m !== 'path');
}

$('#srcBrowser').onclick = () => setSrcMode('browser');
$('#srcPath').onclick = () => setSrcMode('path');
$('#btnPickFiles').onclick = (e) => { e.stopPropagation(); $('#fileInput').click(); };
$('#btnPickFolder').onclick = (e) => { e.stopPropagation(); $('#folderInput').click(); };
$('#dropzone').onclick = () => $('#fileInput').click();

$('#dropzone').addEventListener('dragover', (e) => { e.preventDefault(); $('#dropzone').classList.add('drag'); });
$('#dropzone').addEventListener('dragleave', () => $('#dropzone').classList.remove('drag'));
$('#dropzone').addEventListener('drop', (e) => {
  e.preventDefault();
  $('#dropzone').classList.remove('drag');
  addFiles([...e.dataTransfer.files]);
});
$('#fileInput').onchange = (e) => { addFiles([...e.target.files]); e.target.value = ''; };
$('#folderInput').onchange = (e) => { addFiles([...e.target.files]); e.target.value = ''; };

const IMG_RE = /\.(jpe?g|png|webp|bmp|tiff?|gif)$/i;

function addFiles(files) {
  const list = files.filter(f => IMG_RE.test(f.name));
  if (!list.length) { toast('未找到支持的图片文件', true); return; }
  state.sid = 'u' + Math.random().toString(36).slice(2, 10);
  state.pendingFiles = list.map(f => ({
    file: f,
    rel: f.webkitRelativePath ? f.webkitRelativePath.replace(/\/[^/]*$/, '') : '',
  }));
  state.uploaded = 0;
  state._lastNames = list.map(f => f.webkitRelativePath || f.name);
  renderUploadSummary('准备上传…');
  uploadNext();
}

async function uploadNext() {
  if (!state.pendingFiles.length) {
    state.ready = true;
    state.fileCount = state.uploaded;
    renderUploadSummary(null);
    updateSteps();
    toast(`已添加 ${state.fileCount} 张图片`);
    return;
  }
  const sid = state.sid;
  const item = state.pendingFiles[0];
  renderUploadSummary(`上传中 ${state.uploaded + 1}/${state.uploaded + state.pendingFiles.length}：${item.file.name}`);
  try {
    const q = `sid=${sid}&name=${encodeURIComponent(item.file.name)}&rel=${encodeURIComponent(item.rel)}`;
    const res = await fetch('/api/upload?' + q, { method: 'POST', body: item.file });
    const data = await res.json();
    if (state.sid !== sid) return;
    if (!res.ok) throw new Error(data.error || '上传失败');
    state.pendingFiles.shift();
    state.uploaded++;
    uploadNext();
  } catch (err) {
    if (state.sid !== sid) return;
    toast(`上传失败：${item.file.name} — ${err.message}`, true);
    renderUploadSummary(null, err.message);
  }
}

function renderUploadSummary(text, err) {
  const box = $('#uploadSummary');
  if (err) { box.innerHTML = `<div class="file-summary" style="background:#fee2e2;color:#b91c1c;">❌ ${err}</div>`; state.ready = false; updateSteps(); return; }
  if (text) { box.innerHTML = `<div class="file-summary">⏳ ${text}</div>`; return; }
  const first = `已就绪 ${state.fileCount} 张图片`;
  box.innerHTML = `
    <div class="file-summary"><span>✅ ${first}${state.fileCount > 100 ? '（列表仅显示前 100 条）' : ''}</span><a id="clearFiles">清空重新选择</a></div>
    <div class="file-list" id="fileListBox"></div>`;
  $('#clearFiles').onclick = clearFiles;
  // 展示本次已选文件名，最多 100 条
  const lb = $('#fileListBox');
  (state._lastNames || []).slice(0, 100).forEach(n => {
    lb.insertAdjacentHTML('beforeend', `<div class="row"><span class="nm">${escapeHtml(n)}</span></div>`);
  });
}

function escapeHtml(s) { return s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function clearFiles() {
  state.sid = null; state.ready = false; state.fileCount = 0; state._lastNames = [];
  state.pendingFiles = []; state.uploaded = 0;
  $('#uploadSummary').innerHTML = '';
  updateSteps();
}

// 扫描本地路径
$('#btnScan').onclick = async () => {
  const p = $('#localPath').value.trim();
  if (!p) { toast('请填写本地路径', true); return; }
  try {
    const d = await api('/api/scan', { path: p });
    state.sid = d.sid;
    state.ready = true;
    state.fileCount = d.count;
    state._lastNames = d.sample;
    $('#scanSummary').innerHTML = `
      <div class="file-summary"><span>✅ 找到 ${d.count} 张图片：${escapeHtml(d.label)}</span></div>
      <div class="file-list">${d.sample.map(n => `<div class="row"><span class="nm">${escapeHtml(n)}</span></div>`).join('')}
      ${d.count > d.sample.length ? `<div class="row"><span class="nm" style="color:var(--slate-400)">… 等共 ${d.count} 个文件</span></div>` : ''}</div>`;
    updateSteps();
  } catch (err) {
    state.ready = false; updateSteps();
    $('#scanSummary').innerHTML = `<div class="file-summary" style="background:#fee2e2;color:#b91c1c;">❌ ${err.message}</div>`;
  }
};
$('#localPath').addEventListener('keydown', e => { if (e.key === 'Enter') $('#btnScan').click(); });

/* ---------------- 参数设置 ---------------- */
$$('#scaleModes .opt-card').forEach(card => {
  card.onclick = () => {
    $$('#scaleModes .opt-card').forEach(c => c.classList.remove('on'));
    card.classList.add('on');
    state.scaleMode = card.dataset.mode;
    const isWH = state.scaleMode === 'width' || state.scaleMode === 'height';
    $('#scaleValueRow').classList.toggle('hidden', !isWH);
    $('#scaleValueLabel').textContent = state.scaleMode === 'width' ? '目标宽度（px）' : '目标高度（px）';
    $('#scaleHint').textContent = state.scaleMode.startsWith('x')
      ? 'AI 放大使用 realesrgan-x4plus-anime 模型（GPU 加速），2/3 倍为模型放大后高质量降采样。'
      : (isWH ? '使用高质量 Lanczos 算法缩放，另一边按原始宽高比自适应。' : '保持原始尺寸，仅做格式转换等后续处理。');
    updateSteps();
  };
});

$('#sharpenOn').onchange = () => {
  const on = $('#sharpenOn').checked;
  $('#sharpenRow').classList.toggle('hidden', !on);
  $('#sharpenHint').classList.toggle('hidden', !on);
  updateSteps();
};
$('#sharpenStrength').oninput = () => { $('#sharpenVal').value = $('#sharpenStrength').value; };
$('#sharpenVal').oninput = () => {
  let v = Math.max(0, Math.min(100, +$('#sharpenVal').value || 0));
  $('#sharpenStrength').value = v;
};
$('#quality').oninput = () => { $('#qualityVal').value = $('#quality').value; };
$('#qualityVal').oninput = () => {
  let v = Math.max(1, Math.min(100, +$('#qualityVal').value || 80));
  $('#quality').value = v;
};

$$('#fmtRow .fmt-pill').forEach(p => {
  p.onclick = () => {
    $$('#fmtRow .fmt-pill').forEach(x => x.classList.remove('on'));
    p.classList.add('on');
    state.format = p.dataset.fmt;
    $('#qualityRow').style.display = state.format === 'png' ? 'none' : 'flex';
    updateSteps();
  };
});

/* ---------------- 开始处理 ---------------- */
$('#btnStart').onclick = async () => {
  if (!state.ready || !state.sid) { toast('请先选择图片', true); return; }
  const body = {
    sid: state.sid,
    options: {
      scaleMode: state.scaleMode,
      scaleValue: +$('#scaleValue').value || 1000,
      sharpen: $('#sharpenOn').checked,
      sharpenStrength: +$('#sharpenStrength').value,
      format: state.format,
      quality: +$('#quality').value,
    },
  };
  try {
    const d = await api('/api/process', body);
    state.job = d.job;
    $('#progressCard').classList.remove('hidden');
    $('#doneActions').classList.add('hidden');
    $('#outPath').classList.add('hidden');
    $('#resultList').innerHTML = '';
    $('#btnStart').disabled = true;
    $('#btnStart').innerHTML = '<span class="spinner"></span> 处理中…';
    $('#progressCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    poll();
  } catch (err) {
    toast(err.message, true);
  }
};

async function poll() {
  clearTimeout(state.pollTimer);
  try {
    const d = await api(`/api/status?job=${state.job}`);
    renderProgress(d);
    if (d.state !== 'done') state.pollTimer = setTimeout(poll, 600);
    else onDone(d);
  } catch (err) {
    state.pollTimer = setTimeout(poll, 1200);
  }
}

const ST_ICON = { pending: '⏳', processing: '⚙️', done: '✅', error: '❌', cancelled: '⏹️' };

function renderProgress(d) {
  const pct = d.total ? Math.round(d.done / d.total * 100) : 0;
  $('#progressPct').textContent = pct + '%';
  $('#progressFill').style.width = pct + '%';
  $('#progressText').textContent = d.state === 'done'
    ? `处理完成：成功 ${d.ok} 张${d.err ? `，失败 ${d.err} 张` : ''}`
    : `处理中 ${d.done}/${d.total}（成功 ${d.ok}${d.err ? ` / 失败 ${d.err}` : ''}）`;
  const list = $('#resultList');
  const keepScroll = list.scrollTop;
  list.innerHTML = d.files.map((f, i) => {
    const nm = escapeHtml(f.out_name || f.name);
    const nt = f.status === 'done'
      ? `${escapeHtml(f.note || '')}${f.bytes != null ? ' · ' + fmtSize(f.bytes) : ''}${f.ms != null ? ' · ' + f.ms + 'ms' : ''}`
      : escapeHtml(f.note || (f.status === 'pending' ? '等待处理' : f.status === 'processing' ? '处理中…' : ''));
    const lnk = f.status === 'done' ? `<a class="lnk" href="#" onclick="saveOne(${i});return false;">保存</a>` : '';
    return `<div class="result-row ${f.status === 'error' ? 'err' : ''}">
      <span class="st">${ST_ICON[f.status] || ''}</span>
      <span class="nm">${nm}</span><span class="nt">${nt}</span>${lnk}</div>`;
  }).join('');
  list.scrollTop = keepScroll;
}

function saveOne(i) {
  toast('已交给浏览器下载，文件会保存到默认下载文件夹');
  window.location.href = `/api/preview?job=${state.job}&i=${i}`;
}

function saveAll() {
  toast('已下载 ZIP 到默认下载文件夹，解压后即为图片');
  window.location.href = `/api/zip?job=${state.job}`;
}


function onDone(d) {
  $('#btnStart').disabled = false;
  $('#btnStart').textContent = '开始批处理';
  $('#outPath').textContent = '📁 保存位置：' + (state.downloadDir || '浏览器默认下载文件夹');
  $('#outPath').classList.remove('hidden');
  $('#doneActions').classList.remove('hidden');
  updateSteps();
  if (d.err === 0) toast(`全部 ${d.ok} 张处理完成 🎉`);
  else toast(`完成：成功 ${d.ok} 张，失败 ${d.err} 张`, d.err > 0);
}

$('#btnSaveAll').onclick = saveAll;
$('#btnZip').onclick = () => {
  window.location.href = `/api/zip?job=${state.job}`;
};
$('#btnOpenFolder').onclick = async () => {
  try { await api('/api/open', { path: '' }); }
  catch (err) { toast(err.message, true); }
};
$('#btnNewTask').onclick = () => {
  clearTimeout(state.pollTimer);
  state.job = null; state.sid = null; state.ready = false; state.fileCount = 0;
  state._lastNames = [];
  $('#progressCard').classList.add('hidden');
  $('#uploadSummary').innerHTML = '';
  $('#scanSummary').innerHTML = '';
  $('#localPath').value = '';
  updateSteps();
  window.scrollTo({ top: 0, behavior: 'smooth' });
};

/* ---------------- 初始化 ---------------- */
ping();
updateSteps();
