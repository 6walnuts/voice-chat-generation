/* 中文语音数据采集 —— 纯浏览器录音，原始 PCM 直接编码 WAV（不经过有损压缩）。 */
'use strict';

const $ = id => document.getElementById(id);

const state = {
  batches: [],        // [{id,name,tip,items:[{id,text}]}]
  flat: [],           // 全部句子按顺序展开
  index: 0,           // 当前句子在 flat 中的下标
  recorded: {},       // id -> meta（服务器返回）
  totalSec: 0,
  recording: false,
  pending: null,      // {blob,dur,peak,sr} 待保存的录音
  saving: false,
  // 录音期资源
  ctx: null, stream: null, source: null, processor: null,
  chunks: [], sampleRate: 48000, startTime: 0, timerId: null, peak: 0,
};

/* ---------------- 初始化 ---------------- */

async function init() {
  try {
    const [corpus, progress] = await Promise.all([
      fetch('/api/sentences').then(r => r.json()),
      fetch('/api/progress').then(r => r.json()),
    ]);
    state.batches = corpus.batches;
    state.flat = corpus.batches.flatMap(b =>
      b.items.map(it => ({ ...it, batch: b.id, batchName: b.name, tip: b.tip })));
    state.recorded = progress.recorded || {};
    state.totalSec = progress.total_sec || 0;

    const firstNew = state.flat.findIndex(s => !state.recorded[s.id]);
    state.index = firstNew === -1 ? 0 : firstNew;
    renderTabs();
    renderSentence();
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
    el.className = 'tab' + (cur && cur.batch === b.id ? ' active' : '');
    el.innerHTML = `${b.id}·${b.name}<span class="cnt">${done}/${b.items.length}</span>`;
    el.onclick = () => {
      const idx = state.flat.findIndex(s => s.batch === b.id && !state.recorded[s.id]);
      goTo(idx !== -1 ? idx : state.flat.findIndex(s => s.batch === b.id));
    };
    tabs.appendChild(el);
  }
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
  hideWarn();
  renderTabs();
}

function renderStatus() {
  const done = Object.keys(state.recorded).length;
  $('status-progress').textContent = `进度 ${done} / ${state.flat.length}`;
  const m = Math.floor(state.totalSec / 60), sec = Math.round(state.totalSec % 60);
  $('status-duration').textContent = `已录 ${m} 分 ${sec} 秒`;
}

function showWarn(msg) { const w = $('warn-area'); w.textContent = msg; w.hidden = false; }
function hideWarn() { $('warn-area').hidden = true; }

function clearPending() {
  state.pending = null;
  $('preview-area').hidden = true;
  const a = $('preview-audio');
  if (a.src) { URL.revokeObjectURL(a.src); a.removeAttribute('src'); }
}

/* ---------------- 录音 ---------------- */

async function startRecording() {
  if (state.recording) return;
  clearPending();
  hideWarn();
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
    showWarn('无法访问麦克风：' + e.message + '。请检查浏览器权限，并确认通过 http://127.0.0.1 访问。');
    return;
  }

  state.ctx = new (window.AudioContext || window.webkitAudioContext)();
  state.sampleRate = state.ctx.sampleRate;
  state.source = state.ctx.createMediaStreamSource(state.stream);
  state.processor = state.ctx.createScriptProcessor(4096, 1, 1);
  state.chunks = [];
  state.peak = 0;

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
    $('level-bar').style.width = Math.min(100, rms * 350) + '%';
  };

  // processor 需要接到 destination 才会持续触发；用 0 增益避免把麦克风声放出来
  const mute = state.ctx.createGain();
  mute.gain.value = 0;
  state.source.connect(state.processor);
  state.processor.connect(mute);
  mute.connect(state.ctx.destination);

  state.recording = true;
  state.startTime = performance.now();
  const btn = $('btn-record');
  btn.textContent = '■ 停止录音';
  btn.classList.add('recording');
  state.timerId = setInterval(() => {
    $('timer').textContent = ((performance.now() - state.startTime) / 1000).toFixed(1) + 's';
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

  const btn = $('btn-record');
  btn.textContent = '● 开始录音';
  btn.classList.remove('recording');
  $('level-bar').style.width = '0%';

  const total = state.chunks.reduce((n, c) => n + c.length, 0);
  if (!total) { showWarn('没有采集到音频数据，请重试。'); return; }
  let pcm = new Float32Array(total);
  let off = 0;
  for (const c of state.chunks) { pcm.set(c, off); off += c.length; }
  state.chunks = [];

  pcm = trimSilence(pcm, state.sampleRate);
  const dur = pcm.length / state.sampleRate;
  if (dur < 0.6) { showWarn('录音太短（不足 0.6 秒），请重录。'); return; }

  const blob = encodeWav(pcm, state.sampleRate);
  state.pending = { blob, dur, peak: state.peak, sr: state.sampleRate };
  const a = $('preview-audio');
  a.src = URL.createObjectURL(blob);
  $('preview-area').hidden = false;
  $('timer').textContent = dur.toFixed(1) + 's';

  if (state.peak >= 0.985) showWarn('⚠ 音量过大出现削波（爆音），建议离麦克风远一点后重录。');
  else if (state.peak < 0.08) showWarn('⚠ 音量偏小，建议靠近麦克风或调大输入音量后重录。');
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

/* ---------------- 保存与导航 ---------------- */

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
    else { renderSentence(); showWarn('🎉 全部录完了！现在可以运行 python3 scripts/export_dataset.py 导出训练集。'); }
  } catch (e) {
    showWarn('保存失败：' + e.message);
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
$('btn-discard').onclick = () => { clearPending(); hideWarn(); };
$('btn-prev').onclick = () => goTo(state.index - 1);
$('btn-next').onclick = () => goTo(state.index + 1);
$('device-select').onchange = e => localStorage.setItem('vc_device', e.target.value);

document.addEventListener('keydown', ev => {
  if (ev.target.tagName === 'SELECT' || ev.target.tagName === 'INPUT') return;
  if (ev.code === 'Space') { ev.preventDefault(); $('btn-record').click(); }
  else if (ev.code === 'Enter') { ev.preventDefault(); save(); }
  else if (ev.code === 'KeyR') { clearPending(); hideWarn(); }
  else if (ev.code === 'ArrowLeft') goTo(state.index - 1);
  else if (ev.code === 'ArrowRight') goTo(state.index + 1);
});

populateDevices();
init();
