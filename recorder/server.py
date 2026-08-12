#!/usr/bin/env python3
"""录音采集服务器（仅用 Python 标准库，无需 pip 安装任何东西）。

用法：
    python3 recorder/server.py            # 默认 http://127.0.0.1:8380
    python3 recorder/server.py --port 9000

两种采集模式：
1. 照稿朗读：按语料逐句录音，音频存 dataset/wavs/<句子ID>.wav，
   文本天然精确对齐。
2. 自由说话：随便聊 1~5 分钟，上传后按停顿自动切成 2~10 秒小段
   （存为 dataset/wavs/S###.wav，原始长录音留档 dataset/raw/），
   在网页里逐段校对文字后确认入库。可用 scripts/transcribe_pending.py
   先做语音识别草稿，校对更快。

每次入库都在 dataset/meta.jsonl 追加一行元数据（重录覆盖 wav 并追加新行，
以最后一行为准）。待校对队列存 dataset/pending.jsonl。

注意：浏览器只在 localhost / https 下允许使用麦克风，请在有麦克风的
电脑上本机运行；远程机器可用 SSH 端口转发（ssh -L 8380:127.0.0.1:8380 ...）。
"""

import argparse
import array
import json
import re
import sys
import threading
import time
import wave
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent.parent          # 项目根目录
STATIC = Path(__file__).resolve().parent / "static"
CORPUS_FILE = BASE / "corpus" / "sentences.json"

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
SEG_ID_RE = re.compile(r"^S(\d+)$")
MAX_BODY = 512 * 1024 * 1024  # 512 MB，容纳 5 分钟 48kHz 单声道绰绰有余

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".wav": "audio/wav",
}

STATE_LOCK = threading.Lock()  # 串行化 meta/pending 的读改写


def load_corpus():
    data = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))
    id_to_text = {}
    for batch in data["batches"]:
        for item in batch["items"]:
            id_to_text[item["id"]] = item["text"]
    return data, id_to_text


def read_jsonl(path: Path):
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                    encoding="utf-8")


def read_meta(dataset_dir: Path):
    """meta.jsonl 按 id 去重（后写覆盖先写），并核对 wav 是否存在。"""
    records = {}
    for rec in read_jsonl(dataset_dir / "meta.jsonl"):
        if "id" in rec:
            records[rec["id"]] = rec
    wavs = dataset_dir / "wavs"
    return {k: v for k, v in records.items() if (wavs / f"{k}.wav").exists()}


# ---------------- 自由说话：按停顿切分 ----------------

def load_wav_samples(path: Path):
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        samples = array.array("h")
        samples.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        samples.byteswap()
    return samples, sr


def write_wav_samples(path: Path, samples, sr):
    seg = array.array("h", samples)
    if sys.byteorder == "big":
        seg.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(seg.tobytes())


def slice_by_silence(samples, sr, min_seg=1.2, max_seg=9.5, min_sil=0.35, pad=0.15):
    """能量门限切分，返回 [(起始采样, 结束采样)]。段边界尽量落在停顿处。"""
    win = max(1, int(sr * 0.05))  # 50ms 窗
    nwin = len(samples) // win
    if nwin == 0:
        return []
    peaks = []
    for i in range(nwin):
        chunk = samples[i * win:(i + 1) * win]  # array 切片 + C 级 max/min，避免逐样本循环
        peaks.append(max(max(chunk), -min(chunk)) / 32768.0)
    ranked = sorted(peaks)
    floor = ranked[int(nwin * 0.1)]         # 10 分位当噪声底
    p90 = ranked[min(nwin - 1, int(nwin * 0.9))]
    # 门限=噪声底×2.5，但不得超过信号 90 分位的一半——
    # 否则几乎不停顿的连续语音会把「噪声底」估成信号本身，全被误判为静音
    thr = max(min(max(floor * 2.5, 0.006), 0.5 * p90), 0.006)
    voiced = [p > thr for p in peaks]

    # 桥接短于 min_sil 的静音缺口（词间小停顿不切）
    gap_win = max(1, int(min_sil / 0.05))
    i = 0
    while i < nwin:
        if voiced[i]:
            i += 1
            continue
        j = i
        while j < nwin and not voiced[j]:
            j += 1
        if 0 < i and j < nwin and (j - i) < gap_win:
            for k in range(i, j):
                voiced[k] = True
        i = j

    # 提取有声区间（秒），太碎的丢掉
    intervals, i = [], 0
    while i < nwin:
        if voiced[i]:
            j = i
            while j < nwin and voiced[j]:
                j += 1
            if (j - i) * 0.05 >= 0.3:
                intervals.append((i * 0.05, j * 0.05))
            i = j
        else:
            i += 1

    # 贪心打包成 ≤ max_seg 的段
    groups, cur = [], None
    for a, b in intervals:
        if b - a > max_seg:  # 单个区间就超长 → 均分硬切
            if cur:
                groups.append(cur)
                cur = None
            n_parts = int((b - a) / max_seg) + 1
            step = (b - a) / n_parts
            groups.extend((a + k * step, a + (k + 1) * step) for k in range(n_parts))
            continue
        if cur is None:
            cur = (a, b)
        elif b - cur[0] <= max_seg:
            cur = (cur[0], b)
        else:
            groups.append(cur)
            cur = (a, b)
    if cur:
        groups.append(cur)

    total = len(samples) / sr
    out = []
    for a, b in groups:
        if b - a < min_seg:
            continue
        a = max(0.0, a - pad)
        b = min(total, b + pad)
        out.append((int(a * sr), int(b * sr)))
    return out


class Handler(BaseHTTPRequestHandler):
    corpus = None
    id_to_text = None
    dataset_dir = None

    # ---------- 基础 ----------

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_wav_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 44 or length > MAX_BODY:
            return None
        body = self.rfile.read(length)
        if body[:4] != b"RIFF" or body[8:12] != b"WAVE":
            return None
        return body

    # ---------- GET ----------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            path = "/index.html"

        if path == "/api/sentences":
            self._send(200, self.corpus)
            return

        if path == "/api/progress":
            with STATE_LOCK:
                records = read_meta(self.dataset_dir)
            total = sum(float(r.get("dur", 0)) for r in records.values())
            self._send(200, {"recorded": records, "total_sec": round(total, 2)})
            return

        if path == "/api/pending":
            with STATE_LOCK:
                rows = read_jsonl(self.dataset_dir / "pending.jsonl")
                wavs = self.dataset_dir / "wavs"
                rows = [r for r in rows if (wavs / f"{r.get('id', '')}.wav").exists()]
            self._send(200, {"pending": rows})
            return

        m = re.match(r"^/api/audio/([A-Za-z0-9_-]{1,32})\.wav$", path)
        if m:
            wav = self.dataset_dir / "wavs" / f"{m.group(1)}.wav"
            if wav.exists():
                self._send(200, wav.read_bytes(), "audio/wav")
            else:
                self._send(404, {"error": "not recorded"})
            return

        candidate = (STATIC / path.lstrip("/")).resolve()
        if candidate.is_file() and STATIC in candidate.parents:
            ctype = CONTENT_TYPES.get(candidate.suffix, "application/octet-stream")
            self._send(200, candidate.read_bytes(), ctype)
        else:
            self._send(404, {"error": "not found"})

    # ---------- POST ----------

    def do_POST(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        route = parsed.path
        try:
            if route == "/api/save":
                self._save_scripted(qs)
            elif route == "/api/save_raw":
                self._save_raw(qs)
            elif route == "/api/confirm":
                self._confirm(qs)
            elif route == "/api/discard":
                self._discard(qs)
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:  # 本地工具，把错误直接回给页面便于排查
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _append_meta(self, rec):
        with (self.dataset_dir / "meta.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _totals(self):
        records = read_meta(self.dataset_dir)
        total = sum(float(r.get("dur", 0)) for r in records.values())
        return len(records), round(total, 2)

    def _save_scripted(self, qs):
        """照稿朗读：id 必须在语料里，文本以语料为准。"""
        sid = (qs.get("id") or [""])[0]
        if not ID_RE.match(sid) or sid not in self.id_to_text:
            self._send(400, {"error": f"未知的句子 id: {sid!r}"})
            return
        body = self._read_wav_body()
        if not body:
            self._send(400, {"error": "不是合法的 WAV 数据"})
            return
        wav_dir = self.dataset_dir / "wavs"
        wav_dir.mkdir(parents=True, exist_ok=True)
        (wav_dir / f"{sid}.wav").write_bytes(body)
        with STATE_LOCK:
            self._append_meta({
                "id": sid,
                "text": self.id_to_text[sid],
                "dur": round(float((qs.get("dur") or ["0"])[0]), 3),
                "sr": int((qs.get("sr") or ["0"])[0]),
                "peak": round(float((qs.get("peak") or ["0"])[0]), 4),
                "ts": int(time.time()),
            })
            count, total = self._totals()
        self._send(200, {"ok": True, "count": count, "total_sec": total})

    def _next_ids(self):
        """下一个可用的 S 段号与 take 号。"""
        used = set()
        for rows in (read_jsonl(self.dataset_dir / "meta.jsonl"),
                     read_jsonl(self.dataset_dir / "pending.jsonl")):
            for r in rows:
                m = SEG_ID_RE.match(r.get("id", ""))
                if m:
                    used.add(int(m.group(1)))
        next_seg = max(used) + 1 if used else 1
        raw_dir = self.dataset_dir / "raw"
        takes = [int(m.group(1)) for p in raw_dir.glob("take_*.wav")
                 if (m := re.match(r"take_(\d+)\.wav$", p.name))]
        return next_seg, (max(takes) + 1 if takes else 1)

    def _save_raw(self, qs):
        """自由说话：存原始长录音，按停顿切成待校对小段。"""
        body = self._read_wav_body()
        if not body:
            self._send(400, {"error": "不是合法的 WAV 数据"})
            return
        wav_dir = self.dataset_dir / "wavs"
        raw_dir = self.dataset_dir / "raw"
        wav_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        with STATE_LOCK:
            next_seg, take_no = self._next_ids()
            take_name = f"take_{take_no:03d}"
            raw_path = raw_dir / f"{take_name}.wav"
            raw_path.write_bytes(body)

            samples, sr = load_wav_samples(raw_path)
            spans = slice_by_silence(samples, sr)
            segs = []
            for a, b in spans:
                sid = f"S{next_seg:03d}"
                next_seg += 1
                write_wav_samples(wav_dir / f"{sid}.wav", samples[a:b], sr)
                segs.append({"id": sid, "take": take_name,
                             "dur": round((b - a) / sr, 3), "sr": sr,
                             "ts": int(time.time())})
            pending_file = self.dataset_dir / "pending.jsonl"
            rows = read_jsonl(pending_file)
            rows.extend(segs)
            write_jsonl(pending_file, rows)

        print(f"[自由说话] {take_name}: 切出 {len(segs)} 段")
        self._send(200, {"ok": True, "take": take_name, "segments": segs})

    def _confirm(self, qs):
        """校对确认：把待校对段连同人工修正后的文本写入 meta。"""
        sid = (qs.get("id") or [""])[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if 0 < length <= 1024 * 1024 else b"{}"
        try:
            text = (json.loads(body.decode("utf-8")).get("text") or "").strip()
        except (json.JSONDecodeError, UnicodeDecodeError):
            text = ""
        if not SEG_ID_RE.match(sid):
            self._send(400, {"error": f"非法段 id: {sid!r}"})
            return
        if not text:
            self._send(400, {"error": "文本不能为空"})
            return
        with STATE_LOCK:
            pending_file = self.dataset_dir / "pending.jsonl"
            rows = read_jsonl(pending_file)
            hit = next((r for r in rows if r.get("id") == sid), None)
            if hit is None:
                self._send(404, {"error": f"{sid} 不在待校对队列"})
                return
            write_jsonl(pending_file, [r for r in rows if r.get("id") != sid])
            self._append_meta({
                "id": sid, "text": text,
                "dur": hit.get("dur", 0), "sr": hit.get("sr", 0),
                "take": hit.get("take", ""), "ts": int(time.time()),
            })
            count, total = self._totals()
        self._send(200, {"ok": True, "count": count, "total_sec": total})

    def _discard(self, qs):
        """校对丢弃：删掉切出的小段（原始长录音仍留档 raw/）。"""
        sid = (qs.get("id") or [""])[0]
        if not SEG_ID_RE.match(sid):
            self._send(400, {"error": f"非法段 id: {sid!r}"})
            return
        with STATE_LOCK:
            pending_file = self.dataset_dir / "pending.jsonl"
            rows = read_jsonl(pending_file)
            write_jsonl(pending_file, [r for r in rows if r.get("id") != sid])
            wav = self.dataset_dir / "wavs" / f"{sid}.wav"
            if wav.exists():
                wav.unlink()
        self._send(200, {"ok": True})

    def log_message(self, fmt, *args):
        if args and "/api/save" in str(args[0]):
            print(f"[保存] {args[0]}")


def main():
    ap = argparse.ArgumentParser(description="中文语音数据采集服务器")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8380)
    ap.add_argument("--dataset", default=str(BASE / "dataset"),
                    help="数据集输出目录（默认项目根目录下的 dataset）")
    ap.add_argument("--no-browser", action="store_true", help="启动时不自动打开浏览器")
    args = ap.parse_args()

    corpus, id_to_text = load_corpus()
    Handler.corpus = corpus
    Handler.id_to_text = id_to_text
    Handler.dataset_dir = Path(args.dataset).resolve()
    Handler.dataset_dir.mkdir(parents=True, exist_ok=True)

    total = sum(len(b["items"]) for b in corpus["batches"])
    url = f"http://{args.host}:{args.port}/"
    print(f"语料共 {total} 句；数据集目录：{Handler.dataset_dir}")
    print(f"请在浏览器打开：{url}  （Ctrl+C 停止）")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。运行 python3 scripts/export_dataset.py 可导出训练集。")


if __name__ == "__main__":
    main()
