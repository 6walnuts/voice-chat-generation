/* 中文语音数据采集 —— 纯浏览器录音，原始 PCM 直接编码 WAV（不经过有损压缩）。
   两种模式：照稿朗读（read）/ 自由说话（free，录长段→上传切分→逐段校对）。 */
'use strict';

const $ = id => document.getElementById(id);

const state = {
  mode: 'read',       // 'read' | 'free'
  batches: [],        // [{id,name,tip,items:[{id,text}]}]
  flat: [],           // 全部句子按顺序展开
  index: 0,           // 当前句子在 flat 中的下标
  recorded: {},       // id -> meta（服务器返回）
  totalSec: 0,
  pendingSegs: [],    // 自由说话待校对段
  recording: false,
  pending: null,      // 朗读模式：{blob,dur,peak,sr} 待保存
  freeTake: null,     // 自由模式：{blob,dur,peak,sr} 待上传
  saving: false,
  // 录音期资源（两种模式共用同一引擎）
  ctx: null, stream: null, source: null, processor: null,
  chunks: [], sampleRate: 48000, startTime: 0, timerId: null, peak: 0,
};

const FREE_MAX_SEC = 300; // 自由说话单次上限 5 分钟

/* ---------------- 初始化 ---------------- */

async function init() {
  try {
    const [corpus, progress, pending] = await Promise.all([
      fetch('/api/sentences').then(r => r.json()),
      fetch('/api/progress').then(r => r.json()),
      fetch('/api/pending').then(r => r.json()),
    ]);
    state.batches = corpus.batches;
    state.flat = corpus.batches.flatMap(b =>
      b.items.map(it => ({ ...it, batch: b.id, batchName: b.name, tip: b.tip })));
    state.recorded = progress.recorded || {};
    state.totalSec = progress.total_sec || 0;
    state.pendingSegs = pending.pending || [];

    const firstNew = state.flat.findIndex(s => !state.recorded[s.id]);
    state.index = firstNew === -1 ? 0 : firstNew;
    renderTabs();
    renderSentence();
    renderPending();
    renderStatus();
  } catch (e) {
    $('sentence-text').textContent = '加载语料失败：' + e.message +
      '（请通过 python3 recorder/server.py 启动后访问，不要直接打开 html 文件）';
  }
}

/* ---------------- 渲染 ---------------- */

function renderTabs() {
  const tabs = $('tabs');
  tabs.innerHTML = '';
  const cur = state.flat[state.index];
  for (const b of state.batches) {
    const done = b.items.filter(it => state.recorded[it.id]).length;
    const el = document.createElement('button');
    el.className = 'tab' + (state.mode === 'read' && cur && cur.batch === b.id ? ' active' : '');
    el.innerHTML = `${b.id}·${b.name}<span class="cnt">${done}/${b.items.length}</span>`;
    el.onclick = () => {
      setMode('read');
      const idx = state.flat.findIndex(s => s.batch === b.id && !state.recorded[s.id]);
      goTo(idx !== -1 ? idx : state.flat.findIndex(s => s.batch === b.id));
    };
    tabs.appendChild(el);
  }
  const freeDone = Object.keys(state.recorded).filter(id => /^S\d+$/.test(id)).length;
  const el = document.createElement('button');
  el.className = 'tab tab-free' + (state.mode === 'free' ? ' active' : '');
  el.innerHTML = `🗣 自由说话<span class="cnt">${freeDone}段` +
    (state.pendingSegs.length ? `+${state.pendingSegs.length}待校` : '') + '</span>';
  el.onclick = () => setMode('free');
  tabs.appendChild(el);
}

function setMode(mode) {
  if (state.mode === mode) { renderTabs(); return; }
  if (state.recording) stopRecording();
  state.mode = mode;
  $('read-card').hidden = mode !== 'read';
  $('free-card').hidden = mode !== 'free';
  renderTabs();
}

function renderSentence() {
  const s = state.flat[state.index];
  if (!s) return;
  $('sentence-id').textContent = `${s.id} · ${s.batchName}（第 ${state.index + 1}/${state.flat.length} 句）`;
  $('batch-tip').textContent = s.tip || '';
  $('sentence-text').textContent = s.text;

  const rec = state.recorded[s.id];
  $('done-mark').hidden = !rec;
  if (rec) {
    $('done-mark').textContent = `✓ 已录 ${Number(rec.dur).toFixed(1)}s（可重录覆盖）`;
    $('existing-audio').src = `/api/audio/${s.id}.wav?ts=${rec.ts || 0}`;
    $('existing-area').hidden = false;
  } else {
    $('existing-area').hidden = true;
  }
  clearPending();
  hideWarn('warn-area');
  renderTabs();
}

function renderStatus() {
  const done = Object.keys(state.recorded).length;
  $('status-progress').textContent = `进度 ${done} / ${state.flat.length + Object.keys(state.recorded).filter(id => /^S\d+$/.test(id)).length}`;
  const m = Math.floor(state.totalSec / 60), sec = Math.round(state.totalSec % 60);
  $('status-duration').textContent = `已录 ${m} 分 ${sec} 秒`;
  const p = $('status-pending');
  p.hidden = !state.pendingSegs.length;
  p.textContent = `待校对 ${state.pendingSegs.length} 段`;
}

function renderPending() {
  const list = $('pending-list');
  $('pending-section').hidden = !state.pendingSegs.length;
  list.innerHTML = '';
  for (const seg of state.pendingSegs) {
    const item = document.createElement('div');
    item.className = 'pending-item';
    item.innerHTML = `
      <div class="pending-head">
        <span class="sid">${seg.id}</span>
        <span class="pending-dur">${Number(seg.dur).toFixed(1)}s · ${seg.take || ''}</span>
      </div>
      <audio controls src="/api/audio/${seg.id}.wav?ts=${seg.ts || 0}"></audio>
      <textarea rows="2" placeholder="听音频，写下你实际说的字……">${seg.draft || ''}</textarea>
      <div class="preview-btns">
        <button class="btn-save btn-confirm">✔ 确认入库</button>
        <button class="btn-ghost btn-drop">✕ 丢弃这段</button>
      </div>`;
    const ta = item.querySelector('textarea');
    item.querySelector('.btn-confirm').onclick = () => confirmSeg(seg.id, ta.value, item);
    item.querySelector('.btn-drop').onclick = () => discardSeg(seg.id);
    list.appendChild(item);
  }
}

function showWarn(id, msg) { const w = $(id); w.textContent = msg; w.hidden = false; }
function hideWarn(id) { $(id).hidden = true; }

function clearPending() {
  state.pending = null;
  $('preview-area').hidden = true;
  const a = $('preview-audio');
  if (a.src) { URL.revokeObjectURL(a.src); a.removeAttribute('src'); }
}

function clearFreeTake() {
  state.freeTake = null;
  $('free-preview').hidden = true;
  const a = $('free-audio');
  if (a.src) { URL.revokeObjectURL(a.src); a.removeAttribute('src'); }
}

/* ---------------- 录音引擎（双模式共用） ---------------- */

function ui() {
  return state.mode === 'read'
    ? { btn: $('btn-record'), timer: $('timer'), level: $('level-bar'), warn: 'warn-area', startLabel: '● 开始录音', stopLabel: '■ 停止录音' }
    : { btn: $('free-record'), timer: $('free-timer'), level: $('free-level'), warn: 'free-warn', startLabel: '● 开始说话', stopLabel: '■ 停止' };
}

async function startRecording() {
  if (state.recording) return;
  if (state.mode === 'read') clearPending(); else clearFreeTake();
  hideWarn(ui().warn);
  try {
    const deviceId = localStorage.getItem('vc_device') || '';
    // 关闭回声消除/降噪/自动增益：训练数据要尽量保留原始音色
    const constraints = {
      audio: {
        echoCancellation: false, noiseSuppression: false, autoGainControl: false,
        channelCount: 1,
        ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      },
    };
    state.stream = await navigator.mediaDevices.getUserMedia(constraints);
    populateDevices();
  } catch (e) {
    showWarn(ui().warn, '无法访问麦克风：' + e.message + '。请检查浏览器权限，并确认通过 http://127.0.0.1 访问。');
    return;
  }

  state.ctx = new (window.AudioContext || window.webkitAudioContext)();
  state.sampleRate = state.ctx.sampleRate;
  state.source = state.ctx.createMediaStreamSource(state.stream);
  state.processor = state.ctx.createScriptProcessor(4096, 1, 1);
  state.chunks = [];
  state.peak = 0;

  const levelEl = ui().level;
  state.processor.onaudioprocess = ev => {
    const data = ev.inputBuffer.getChannelData(0);
    state.chunks.push(new Float32Array(data));
    let rms = 0;
    for (let i = 0; i < data.length; i++) {
      const v = Math.abs(data[i]);
      if (v > state.peak) state.peak = v;
      rms += data[i] * data[i];
    }
    rms = Math.sqrt(rms / data.length);
    levelEl.style.width = Math.min(100, rms * 350) + '%';
  };

  // processor 需要接到 destination 才会持续触发；用 0 增益避免把麦克风声放出来
  const mute = state.ctx.createGain();
  mute.gain.value = 0;
  state.source.connect(state.processor);
  state.processor.connect(mute);
  mute.connect(state.ctx.destination);

  state.recording = true;
  state.startTime = performance.now();
  const u = ui();
  u.btn.textContent = u.stopLabel;
  u.btn.classList.add('recording');
  state.timerId = setInterval(() => {
    const sec = (performance.now() - state.startTime) / 1000;
    u.timer.textContent = sec.toFixed(1) + 's';
    if (state.mode === 'free' && sec >= FREE_MAX_SEC) stopRecording();
  }, 100);
}

function stopRecording() {
  if (!state.recording) return;
  state.recording = false;
  clearInterval(state.timerId);
  try {
    state.processor.disconnect();
    state.source.disconnect();
    state.stream.getTracks().forEach(t => t.stop());
    state.ctx.close();
  } catch (e) { /* 忽略清理错误 */ }

  const u = ui();
  u.btn.textContent = u.startLabel;
  u.btn.classList.remove('recording');
  u.level.style.width = '0%';

  const total = state.chunks.reduce((n, c) => n + c.length, 0);
  if (!total) { showWarn(u.warn, '没有采集到音频数据，请重试。'); return; }
  let pcm = new Float32Array(total);
  let off = 0;
  for (const c of state.chunks) { pcm.set(c, off); off += c.length; }
  state.chunks = [];

  pcm = trimSilence(pcm, state.sampleRate);
  const dur = pcm.length / state.sampleRate;
  const minDur = state.mode === 'read' ? 0.6 : 2.0;
  if (dur < minDur) { showWarn(u.warn, `录音太短（不足 ${minDur} 秒），请重试。`); return; }

  const blob = encodeWav(pcm, state.sampleRate);
  const take = { blob, dur, peak: state.peak, sr: state.sampleRate };
  u.timer.textContent = dur.toFixed(1) + 's';

  if (state.mode === 'read') {
    state.pending = take;
    $('preview-audio').src = URL.createObjectURL(blob);
    $('preview-area').hidden = false;
  } else {
    state.freeTake = take;
    $('free-audio').src = URL.createObjectURL(blob);
    $('free-preview').hidden = false;
  }

  if (state.peak >= 0.985) showWarn(u.warn, '⚠ 音量过大出现削波（爆音），建议离麦克风远一点后重录。');
  else if (state.peak < 0.08) showWarn(u.warn, '⚠ 音量偏小，建议靠近麦克风或调大输入音量后重录。');
}

/* 掐掉首尾静音，保留 0.25s 余量 */
function trimSilence(pcm, sr, threshold = 0.008) {
  let start = 0, end = pcm.length - 1;
  while (start < pcm.length && Math.abs(pcm[start]) < threshold) start++;
  while (end > start && Math.abs(pcm[end]) < threshold) end--;
  if (start >= end) return pcm; // 整段近乎静音，原样保留并靠音量警告提示
  const pad = Math.round(sr * 0.25);
  start = Math.max(0, start - pad);
  end = Math.min(pcm.length, end + pad);
  return pcm.slice(start, end);
}

function encodeWav(pcm, sr) {
  const buf = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buf);
  const wstr = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  wstr(0, 'RIFF'); v.setUint32(4, 36 + pcm.length * 2, true); wstr(8, 'WAVE');
  wstr(12, 'fmt '); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);              // PCM
  v.setUint16(22, 1, true);              // 单声道
  v.setUint32(24, sr, true);
  v.setUint32(28, sr * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);             // 16-bit
  wstr(36, 'data'); v.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}

/* ---------------- 朗读模式：保存与导航 ---------------- */

async function save() {
  if (!state.pending || state.saving) return;
  state.saving = true;
  $('btn-save').disabled = true;
  const s = state.flat[state.index];
  const p = state.pending;
  try {
    const q = `id=${encodeURIComponent(s.id)}&dur=${p.dur.toFixed(3)}&sr=${p.sr}&peak=${p.peak.toFixed(4)}`;
    const resp = await fetch(`/api/save?${q}`, { method: 'POST', body: p.blob });
    const j = await resp.json();
    if (!resp.ok) throw new Error(j.error || resp.status);
    state.recorded[s.id] = { dur: p.dur, ts: Date.now() / 1000 };
    state.totalSec = j.total_sec;
    renderStatus();
    clearPending();
    const next = state.flat.findIndex((x, i) => i > state.index && !state.recorded[x.id]);
    const anyNew = next !== -1 ? next : state.flat.findIndex(x => !state.recorded[x.id]);
    if (anyNew !== -1) goTo(anyNew);
    else { renderSentence(); showWarn('warn-area', '🎉 朗读语料全部录完！可以去「自由说话」补自然语气，或运行 python3 scripts/export_dataset.py 导出训练集。'); }
  } catch (e) {
    showWarn('warn-area', '保存失败：' + e.message);
  } finally {
    state.saving = false;
    $('btn-save').disabled = false;
  }
}

function goTo(idx) {
  if (idx < 0 || idx >= state.flat.length) return;
  if (state.recording) stopRecording();
  state.index = idx;
  renderSentence();
}

/* ---------------- 自由模式：上传与校对 ---------------- */

async function uploadFreeTake() {
  if (!state.freeTake || state.saving) return;
  state.saving = true;
  const btn = $('free-upload');
  btn.disabled = true;
  btn.textContent = '⏳ 切分中……';
  const p = state.freeTake;
  try {
    const q = `dur=${p.dur.toFixed(3)}&sr=${p.sr}&peak=${p.peak.toFixed(4)}`;
    const resp = await fetch(`/api/save_raw?${q}`, { method: 'POST', body: p.blob });
    const j = await resp.json();
    if (!resp.ok) throw new Error(j.error || resp.status);
    clearFreeTake();
    await refreshPending();
    showWarn('free-warn', j.segments.length
      ? `✔ 已切出 ${j.segments.length} 段（${j.take}），请在下方逐段校对。`
      : '这段录音没有切出有效语音（可能太安静），请重录。');
  } catch (e) {
    showWarn('free-warn', '上传失败：' + e.message);
  } finally {
    state.saving = false;
    btn.disabled = false;
    btn.textContent = '⬆ 上传并按停顿切分';
  }
}

async function refreshPending() {
  const j = await fetch('/api/pending').then(r => r.json());
  state.pendingSegs = j.pending || [];
  renderPending();
  renderStatus();
  renderTabs();
}

async function confirmSeg(id, text, itemEl) {
  text = (text || '').trim();
  if (!text) {
    itemEl.querySelector('textarea').focus();
    showWarn('free-warn', `${id}：请先写下这段说的字，再确认入库。`);
    return;
  }
  try {
    const resp = await fetch(`/api/confirm?id=${encodeURIComponent(id)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const j = await resp.json();
    if (!resp.ok) throw new Error(j.error || resp.status);
    state.recorded[id] = { dur: 0, ts: Date.now() / 1000 };
    state.totalSec = j.total_sec;
    hideWarn('free-warn');
    await refreshPending();
  } catch (e) {
    showWarn('free-warn', '确认失败：' + e.message);
  }
}

async function discardSeg(id) {
  try {
    const resp = await fetch(`/api/discard?id=${encodeURIComponent(id)}`, { method: 'POST' });
    if (!resp.ok) throw new Error((await resp.json()).error || resp.status);
    await refreshPending();
  } catch (e) {
    showWarn('free-warn', '丢弃失败：' + e.message);
  }
}

/* ---------------- 设备选择 ---------------- */

async function populateDevices() {
  try {
    const devs = await navigator.mediaDevices.enumerateDevices();
    const sel = $('device-select');
    const saved = localStorage.getItem('vc_device') || '';
    sel.innerHTML = '<option value="">默认设备</option>';
    for (const d of devs.filter(d => d.kind === 'audioinput')) {
      const opt = document.createElement('option');
      opt.value = d.deviceId;
      opt.textContent = d.label || `麦克风 ${sel.length}`;
      if (d.deviceId === saved) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch (e) { /* 未授权前拿不到设备名，忽略 */ }
}

/* ---------------- 事件绑定 ---------------- */

$('btn-record').onclick = () => state.recording ? stopRecording() : startRecording();
$('btn-save').onclick = save;
$('btn-discard').onclick = () => { clearPending(); hideWarn('warn-area'); };
$('btn-prev').onclick = () => goTo(state.index - 1);
$('btn-next').onclick = () => goTo(state.index + 1);

$('free-record').onclick = () => state.recording ? stopRecording() : startRecording();
$('free-upload').onclick = uploadFreeTake;
$('free-redo').onclick = () => { clearFreeTake(); hideWarn('free-warn'); };

$('device-select').onchange = e => localStorage.setItem('vc_device', e.target.value);

document.addEventListener('keydown', ev => {
  const tag = ev.target.tagName;
  if (tag === 'SELECT' || tag === 'INPUT' || tag === 'TEXTAREA') return;
  if (ev.code === 'Space') {
    ev.preventDefault();
    (state.mode === 'read' ? $('btn-record') : $('free-record')).click();
    return;
  }
  if (state.mode !== 'read') return;
  if (ev.code === 'Enter') { ev.preventDefault(); save(); }
  else if (ev.code === 'KeyR') { clearPending(); hideWarn('warn-area'); }
  else if (ev.code === 'ArrowLeft') goTo(state.index - 1);
  else if (ev.code === 'ArrowRight') goTo(state.index + 1);
});

populateDevices();
init();
