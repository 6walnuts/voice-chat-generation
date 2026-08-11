#!/usr/bin/env python3
"""把录好的数据集导出为 GPT-SoVITS 可直接使用的标注文件（.list）。

用法：
    python3 scripts/export_dataset.py                # 默认导出 dataset/gptsovits.list
    python3 scripts/export_dataset.py --speaker me   # 自定义说话人名

输出格式（GPT-SoVITS 标准标注格式，每行一条）：
    wavs/A01.wav|<speaker>|ZH|句子文本

同时做质量体检：时长异常、削波（爆音）、音量过小的条目会逐条列出，
建议回到录音页面重录后再导出一次。仅用 Python 标准库。
"""

import argparse
import array
import json
import sys
import wave
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # voice-chat-generation/


def load_meta(dataset_dir: Path):
    meta_file = dataset_dir / "meta.jsonl"
    if not meta_file.exists():
        sys.exit(f"找不到 {meta_file}，请先运行 recorder/server.py 录音。")
    records = {}
    for line in meta_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            records[rec["id"]] = rec  # 后写覆盖先写（重录以最新为准）
        except (json.JSONDecodeError, KeyError):
            continue
    return records


def inspect_wav(path: Path):
    """返回 (时长秒, 峰值 0~1, 采样率)；非 16-bit 单声道会抛异常。"""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path.name} 不是 16-bit PCM")
        n, sr, ch = w.getnframes(), w.getframerate(), w.getnchannels()
        samples = array.array("h")
        samples.frombytes(w.readframes(n))
    if sys.byteorder == "big":
        samples.byteswap()
    peak = max((abs(s) for s in samples), default=0) / 32768.0
    return n / sr / ch if ch else 0.0, peak, sr


def main():
    ap = argparse.ArgumentParser(description="导出 GPT-SoVITS 训练集标注")
    ap.add_argument("--dataset", default=str(BASE / "dataset"))
    ap.add_argument("--speaker", default="me", help="说话人名（写进标注文件，默认 me）")
    ap.add_argument("--out", default="", help="输出 .list 路径（默认 dataset/gptsovits.list）")
    args = ap.parse_args()

    dataset_dir = Path(args.dataset).resolve()
    wav_dir = dataset_dir / "wavs"
    out_path = Path(args.out) if args.out else dataset_dir / "gptsovits.list"

    records = load_meta(dataset_dir)
    lines, warnings, total_sec = [], [], 0.0
    per_batch = {}

    for sid in sorted(records):
        rec = records[sid]
        wav = wav_dir / f"{sid}.wav"
        if not wav.exists():
            warnings.append(f"{sid}: 缺少音频文件，已跳过")
            continue
        try:
            dur, peak, sr = inspect_wav(wav)
        except Exception as e:
            warnings.append(f"{sid}: 无法读取（{e}），已跳过")
            continue

        text = rec.get("text", "").strip()
        if not text:
            warnings.append(f"{sid}: 缺少文本，已跳过")
            continue

        if dur < 1.5:
            warnings.append(f"{sid}: 时长仅 {dur:.1f}s（建议 2~10s，过短会影响训练）")
        elif dur > 12:
            warnings.append(f"{sid}: 时长 {dur:.1f}s 偏长（建议重录或读快一点）")
        if peak >= 0.985:
            warnings.append(f"{sid}: 检测到削波（爆音，峰值 {peak:.2f}），建议重录")
        elif peak < 0.05:
            warnings.append(f"{sid}: 音量过小（峰值 {peak:.2f}），建议重录")

        total_sec += dur
        batch = sid[0]
        per_batch[batch] = per_batch.get(batch, 0) + 1
        lines.append(f"wavs/{sid}.wav|{args.speaker}|ZH|{text}")

    if not lines:
        sys.exit("没有可导出的录音。")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    m, s = divmod(int(total_sec), 60)
    print(f"✔ 已导出 {len(lines)} 条，共 {m} 分 {s} 秒 → {out_path}")
    print(f"  各批次条数：{'，'.join(f'{k}:{v}' for k, v in sorted(per_batch.items()))}")
    print(f"  音频目录：{wav_dir}")
    if warnings:
        print(f"\n⚠ 质量提醒（{len(warnings)} 条，不影响导出，但建议处理）：")
        for w in warnings:
            print(f"  - {w}")
    if total_sec < 60:
        print("\n提示：总时长不足 1 分钟，勉强可以训练；建议至少录完 A 批（约 3~5 分钟）。")
    print("\n下一步：按 docs/training.md 用 GPT-SoVITS 微调。在其 WebUI 的「1A-训练集格式化」中：")
    print(f"  文本标注文件      = {out_path}")
    print(f"  训练集音频文件目录 = {wav_dir}")


if __name__ == "__main__":
    main()
