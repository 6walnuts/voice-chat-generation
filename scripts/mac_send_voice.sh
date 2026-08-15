#!/usr/bin/env bash
# 把合成好的语音"播放进"虚拟麦克风，供微信 Mac 版录成真正的语音消息。
#
# 原理：BlackHole 是一个虚拟声卡。把系统的"输出"和"输入"都切到它，
# 播放器放出的声音就会原样出现在"麦克风"上——微信录音时听到的就是这段音频。
#
# 一次性准备（终端执行）：
#   brew install blackhole-2ch switchaudio-osx
#   装完 BlackHole 后重启一次微信。
#
# 用法：
#   ./scripts/mac_send_voice.sh 合成的语音.wav        # 3 秒倒计时后开始播放
#   ./scripts/mac_send_voice.sh 合成的语音.wav 5      # 自定义倒计时秒数
#
# 流程：运行脚本 → 倒计时内去微信开始录音（Mac 版默认按住 Fn，或点输入框
# 旁的话筒图标）→ 播放自动进行 → 播完去微信停止录音、发送。脚本结束会把
# 音频设备恢复原样。录音触发保持手动——这是对账号最安全的方式。
#
# 注意：只用于你本人的声音；接收方应当知道或不介意这是合成语音。

set -euo pipefail

WAV="${1:?用法: $0 语音.wav [倒计时秒数,默认3]}"
DELAY="${2:-3}"

[ -f "$WAV" ] || { echo "文件不存在: $WAV"; exit 1; }
command -v SwitchAudioSource >/dev/null 2>&1 || {
  echo "缺少 switchaudio-osx，安装：brew install switchaudio-osx"; exit 1; }
SwitchAudioSource -a | grep -q "BlackHole" || {
  echo "未检测到 BlackHole 虚拟声卡，安装：brew install blackhole-2ch"
  echo "（装完重启微信再试）"; exit 1; }

CUR_IN="$(SwitchAudioSource -c -t input)"
CUR_OUT="$(SwitchAudioSource -c -t output)"
restore() {
  SwitchAudioSource -t input -s "$CUR_IN" >/dev/null 2>&1 || true
  SwitchAudioSource -t output -s "$CUR_OUT" >/dev/null 2>&1 || true
  echo "✔ 已恢复原音频设备（输入:$CUR_IN / 输出:$CUR_OUT）"
}
trap restore EXIT

BH="$(SwitchAudioSource -a -t input | grep -m1 'BlackHole')"
SwitchAudioSource -t input -s "$BH" >/dev/null
SwitchAudioSource -t output -s "$BH" >/dev/null
echo "音频设备已切到 $BH（此期间喇叭不出声、真麦克风不收音）"
echo
echo "‼️  现在切到微信，开始录音（按住 Fn 或点话筒图标）"
for i in $(seq "$DELAY" -1 1); do printf "   %d 秒后开始播放...\r" "$i"; sleep 1; done
echo
afplay "$WAV"
echo "✔ 播放完毕——去微信停止录音并发送"
sleep 1
