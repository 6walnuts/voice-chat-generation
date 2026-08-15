#!/usr/bin/env bash
# 日常一条命令：合成你的声音 → 试听 → 回车确认 → 发进微信。
#
# 用法：
#   ./scripts/say.sh "喂，我在开会，晚点回你。"
#
# 前提：GPT-SoVITS 的 api_v2 服务已在本机运行（见 docs/wechat-voice.md），
# 且已加载你的权重（推荐把权重路径直接写进 tts_infer.yaml，一劳永逸）。
#
# 可选环境变量：
#   REF=参考音频路径（默认 dataset/wavs/S019.wav，换它=换语气）
#   GPT_W / SOVITS_W=权重路径（没写进 yaml 时，本次会话首条消息传一次即可）

set -euo pipefail
TEXT="${1:?用法: $0 \"要说的话\"}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REF="${REF:-$HERE/../dataset/wavs/S019.wav}"
OUT="/tmp/wechat_voice_$$.wav"

python3 "$HERE/tts_client.py" "$TEXT" --ref "$REF" \
  ${GPT_W:+--gpt-weights "$GPT_W"} \
  ${SOVITS_W:+--sovits-weights "$SOVITS_W"} \
  --out "$OUT"

echo "🔊 试听一遍……"
afplay "$OUT"
read -r -p "满意就按回车发送微信；不满意 Ctrl+C 取消（改文本重来）: " _

bash "$HERE/mac_send_voice.sh" "$OUT" 5
rm -f "$OUT"
