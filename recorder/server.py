#!/usr/bin/env python3
"""录音采集服务器（仅用 Python 标准库，无需 pip 安装任何东西）。

用法：
    python3 recorder/server.py            # 默认 http://127.0.0.1:8380
    python3 recorder/server.py --port 9000

浏览器打开后按语料逐句录音，音频保存到 voice-chat-generation/dataset/wavs/<句子ID>.wav，
每次保存同时在 dataset/meta.jsonl 追加一行元数据（重录会覆盖 wav 并追加新行，
以最后一行为准）。

注意：浏览器只在 localhost / https 下允许使用麦克风，所以请在有麦克风的
电脑上本机运行；远程机器可用 SSH 端口转发（ssh -L 8380:127.0.0.1:8380 ...）。
"""

import argparse
import json
import re
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent.parent          # voice-chat-generation/
STATIC = Path(__file__).resolve().parent / "static"
CORPUS_FILE = BASE / "corpus" / "sentences.json"

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MAX_BODY = 64 * 1024 * 1024  # 64 MB，足够容纳几分钟的 48kHz 单声道 WAV

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".wav": "audio/wav",
}


def load_corpus():
    data = json.loads(CORPUS_FILE.read_text(encoding="utf-8"))
    id_to_text = {}
    for batch in data["batches"]:
        for item in batch["items"]:
            id_to_text[item["id"]] = item["text"]
    return data, id_to_text


def read_meta(dataset_dir: Path):
    """meta.jsonl 逐行读取，按 id 去重（后写的覆盖先写的），并核对 wav 是否存在。"""
    meta_file = dataset_dir / "meta.jsonl"
    records = {}
    if meta_file.exists():
        for line in meta_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                records[rec["id"]] = rec
            except (json.JSONDecodeError, KeyError):
                continue
    wavs = dataset_dir / "wavs"
    return {k: v for k, v in records.items() if (wavs / f"{k}.wav").exists()}


class Handler(BaseHTTPRequestHandler):
    corpus = None
    id_to_text = None
    dataset_dir = None

    def _send(self, code, body, ctype="application/json; charset=utf-8", cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if not cache:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            path = "/index.html"

        if path == "/api/sentences":
            self._send(200, self.corpus)
            return

        if path == "/api/progress":
            records = read_meta(self.dataset_dir)
            total = sum(float(r.get("dur", 0)) for r in records.values())
            self._send(200, {"recorded": records, "total_sec": round(total, 2)})
            return

        m = re.match(r"^/api/audio/([A-Za-z0-9_-]{1,32})\.wav$", path)
        if m:
            wav = self.dataset_dir / "wavs" / f"{m.group(1)}.wav"
            if wav.exists():
                self._send(200, wav.read_bytes(), "audio/wav")
            else:
                self._send(404, {"error": "not recorded"})
            return

        # 静态文件（限定在 static/ 目录内）
        candidate = (STATIC / path.lstrip("/")).resolve()
        if candidate.is_file() and STATIC in candidate.parents:
            ctype = CONTENT_TYPES.get(candidate.suffix, "application/octet-stream")
            self._send(200, candidate.read_bytes(), ctype)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/save":
            self._send(404, {"error": "not found"})
            return

        qs = parse_qs(parsed.query)
        sid = (qs.get("id") or [""])[0]
        if not ID_RE.match(sid) or sid not in self.id_to_text:
            self._send(400, {"error": f"未知的句子 id: {sid!r}"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 44 or length > MAX_BODY:
            self._send(400, {"error": f"音频大小异常: {length} 字节"})
            return
        body = self.rfile.read(length)
        if body[:4] != b"RIFF" or body[8:12] != b"WAVE":
            self._send(400, {"error": "不是合法的 WAV 数据"})
            return

        wav_dir = self.dataset_dir / "wavs"
        wav_dir.mkdir(parents=True, exist_ok=True)
        (wav_dir / f"{sid}.wav").write_bytes(body)

        rec = {
            "id": sid,
            "text": self.id_to_text[sid],
            "dur": round(float((qs.get("dur") or ["0"])[0]), 3),
            "sr": int((qs.get("sr") or ["0"])[0]),
            "peak": round(float((qs.get("peak") or ["0"])[0]), 4),
            "ts": int(time.time()),
        }
        with (self.dataset_dir / "meta.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        records = read_meta(self.dataset_dir)
        total = sum(float(r.get("dur", 0)) for r in records.values())
        self._send(200, {"ok": True, "count": len(records), "total_sec": round(total, 2)})

    def log_message(self, fmt, *args):
        if "/api/save" in (args[0] if args else ""):
            print(f"[保存] {args[0]}")


def main():
    ap = argparse.ArgumentParser(description="中文语音数据采集服务器")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8380)
    ap.add_argument("--dataset", default=str(BASE / "dataset"),
                    help="数据集输出目录（默认 voice-chat-generation/dataset）")
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
