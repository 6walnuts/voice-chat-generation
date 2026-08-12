#!/usr/bin/env python3
"""给「自由说话」切出的待校对段做语音识别，预填草稿，让人工校对更快。

这是整个项目里唯一需要第三方库的可选环节（不装也完全能用——校对时手写文字即可）：

    pip install faster-whisper          # 首选，本地运行，中文效果好
    python3 scripts/transcribe_pending.py            # 默认 small 模型
    python3 scripts/transcribe_pending.py --model medium   # 更准但更慢

脚本会把识别草稿写进 dataset/pending.jsonl 的 draft 字段；
回到录音页面刷新，草稿会自动出现在校对输入框里。识别只是草稿——
最终以你校对确认的文字为准（这正是本流程的意义）。
"""

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # 项目根目录


def read_jsonl(path: Path):
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def load_model(name: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit(
            "未安装 faster-whisper。安装后重试：\n"
            "    pip install faster-whisper\n"
            "（不想装也可以：直接在录音页面手写每段文字，效果一样，只是慢一点。）"
        )
    print(f"加载 whisper 模型 {name}（首次运行会自动下载）……")
    return WhisperModel(name, device="auto", compute_type="auto")


def main():
    ap = argparse.ArgumentParser(description="为待校对段生成语音识别草稿")
    ap.add_argument("--dataset", default=str(BASE / "dataset"))
    ap.add_argument("--model", default="small",
                    help="whisper 模型：tiny/base/small/medium/large-v3（默认 small）")
    ap.add_argument("--redo", action="store_true", help="已有草稿的段也重新识别")
    args = ap.parse_args()

    dataset_dir = Path(args.dataset).resolve()
    pending_file = dataset_dir / "pending.jsonl"
    rows = read_jsonl(pending_file)
    if not rows:
        sys.exit("待校对队列是空的。先去录音页面「自由说话」录一段并上传切分。")

    todo = [r for r in rows if args.redo or not r.get("draft")]
    if not todo:
        sys.exit("所有待校对段都已有草稿（加 --redo 可全部重识别）。")

    model = load_model(args.model)
    print(f"共 {len(todo)} 段待识别。")

    for i, rec in enumerate(todo, 1):
        wav = dataset_dir / "wavs" / f"{rec['id']}.wav"
        if not wav.exists():
            print(f"[{i}/{len(todo)}] {rec['id']}: 音频缺失，跳过")
            continue
        segments, _info = model.transcribe(
            str(wav), language="zh", beam_size=5, vad_filter=False,
            initial_prompt="以下是简体中文普通话的日常口语。",
        )
        text = "".join(seg.text for seg in segments).strip()
        rec["draft"] = text
        print(f"[{i}/{len(todo)}] {rec['id']}: {text or '(未识别出内容)'}")

    pending_file.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
    print(f"\n✔ 草稿已写入 {pending_file}。回到录音页面刷新，逐段校对后确认入库。")
    print("  注意：识别可能把数字写成阿拉伯数字或繁体字——校对时请改成你实际读音的汉字。")


if __name__ == "__main__":
    main()
