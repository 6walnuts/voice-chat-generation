#!/usr/bin/env python3
"""调用本机 GPT-SoVITS API，用你训练好的模型把任意文本合成为你的声音。

前提：已按 docs/training.md 完成微调，并在 GPT-SoVITS 目录里启动了 API 服务：
    python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml

用法：
    python3 scripts/tts_client.py "你好，这是用我自己的声音合成的。" --ref dataset/wavs/A24.wav
    python3 scripts/tts_client.py "明天下午三点开会。" --ref dataset/wavs/A24.wav --out meeting.wav

--ref 是一条 3~10 秒的参考音频（选一条你录得最满意的即可）。参考音频来自
本项目 dataset/wavs/ 时，其对应文本会自动从 meta.jsonl 里查出，无需手填；
否则请用 --prompt-text 提供参考音频的文本。仅用 Python 标准库。
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # voice-chat-generation/


def lookup_prompt_text(ref: Path):
    meta = ref.parent.parent / "meta.jsonl"
    if not meta.exists():
        return None
    sid = ref.stem
    text = None
    for line in meta.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            if rec.get("id") == sid:
                text = rec.get("text")
        except (json.JSONDecodeError, KeyError):
            continue
    return text


def switch_weights(host: str, kind: str, path: str):
    """让 api_v2 加载指定的微调权重（kind: gpt / sovits）。"""
    from urllib.parse import quote
    url = f"http://{host}/set_{kind}_weights?weights_path={quote(str(Path(path).resolve()))}"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            resp.read()
        print(f"✔ 已加载 {kind} 权重：{path}")
    except urllib.error.HTTPError as e:
        sys.exit(f"加载 {kind} 权重失败（{e.code}）：{e.read().decode('utf-8', 'replace')}")


def main():
    ap = argparse.ArgumentParser(description="GPT-SoVITS 合成客户端")
    ap.add_argument("text", help="要合成的中文文本")
    ap.add_argument("--ref", required=True, help="参考音频路径（3~10 秒的你的录音）")
    ap.add_argument("--prompt-text", default="", help="参考音频对应的文本（dataset 内的录音可自动查出）")
    ap.add_argument("--host", default="127.0.0.1:9880", help="GPT-SoVITS API 地址")
    ap.add_argument("--out", default="out.wav", help="输出音频路径")
    ap.add_argument("--gpt-weights", default="", help="你微调出的 GPT 权重（*.ckpt），本次会话加载一次即可")
    ap.add_argument("--sovits-weights", default="", help="你微调出的 SoVITS 权重（*.pth）")
    args = ap.parse_args()

    if args.gpt_weights:
        switch_weights(args.host, "gpt", args.gpt_weights)
    if args.sovits_weights:
        switch_weights(args.host, "sovits", args.sovits_weights)

    ref = Path(args.ref).resolve()
    if not ref.exists():
        sys.exit(f"参考音频不存在：{ref}")

    prompt_text = args.prompt_text or lookup_prompt_text(ref)
    if not prompt_text:
        sys.exit("无法确定参考音频的文本，请用 --prompt-text 提供。")

    payload = {
        "text": args.text,
        "text_lang": "zh",
        "ref_audio_path": str(ref),
        "prompt_text": prompt_text,
        "prompt_lang": "zh",
        "text_split_method": "cut5",
        "media_type": "wav",
        "streaming_mode": False,
    }
    req = urllib.request.Request(
        f"http://{args.host}/tts",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        sys.exit(f"API 返回错误 {e.code}：{detail}")
    except urllib.error.URLError as e:
        sys.exit(
            f"连不上 {args.host}（{e.reason}）。\n"
            "请先在 GPT-SoVITS 目录启动 API：\n"
            "  python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml\n"
            "并确认已在 WebUI 或 API 中加载了你微调后的 GPT / SoVITS 权重。"
        )

    Path(args.out).write_bytes(audio)
    print(f"✔ 已合成 {len(audio) / 1024:.0f} KB → {args.out}")


if __name__ == "__main__":
    main()
