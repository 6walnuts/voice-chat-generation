# 用你的声音发微信语音（macOS）

训练好的模型 → Mac 本地合成 → 微信语音气泡,全链路指南。

**原理**:BlackHole 是个虚拟声卡("假麦克风")。把合成好的 WAV 播放进它,
微信录音时"听到"的就是这段音频——录下来发出去的就是真正的语音消息,
不改微信、不碰协议、不需要越狱或 root。

## 前提自查

1. **微信 Mac 版 ≥ 4.1.7**(2026 年起支持直接发语音消息):聊天输入框旁有
   **话筒图标**、或按住 **Fn** 能录音,就说明支持。太旧就先升级微信。
2. 已从 Colab 下载 `my_voice_models.zip`(训练完最后一格生成的模型包)。

## 第 1 步:Mac 本地部署推理(一次性,约 20 分钟)

```bash
# 1. 装 GPT-SoVITS(M 芯片 CPU 推理即可,短句几秒~十几秒)
git clone https://github.com/RVC-Boss/GPT-SoVITS.git
cd GPT-SoVITS
python3 -m venv .venv && source .venv/bin/activate
pip install -r extra-req.txt --no-deps && pip install -r requirements.txt
# 国内网络下载底模前先: export HF_ENDPOINT=https://hf-mirror.com
python3 -c "from huggingface_hub import snapshot_download; snapshot_download('lj1995/GPT-SoVITS', local_dir='GPT_SoVITS/pretrained_models')"

# 2. 把你的模型放进来:解压 my_voice_models.zip,
#    将 SoVITS_weights*/ 和 GPT_weights*/ 两个文件夹整个拷到 GPT-SoVITS 目录下

# 3. 启动 API 服务(保持运行)
python3 api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

## 第 2 步:合成一句试试

回到本仓库目录,首次合成时把你的权重加载进去(之后同一会话不用再传):

```bash
python3 scripts/tts_client.py "喂,我现在开会,晚点回你电话。" \
  --ref dataset/wavs/S019.wav \
  --gpt-weights  /路径/GPT-SoVITS/GPT_weights_v2Pro/myvoice-e15.ckpt \
  --sovits-weights /路径/GPT-SoVITS/SoVITS_weights_v2Pro/myvoice_e8_s96.pth \
  --out voice.wav
```

权重文件名以你实际训出的为准(选 e 数最大的);`--ref` 用你最满意的一条录音。
听一下 `voice.wav`,满意再进下一步。

## 第 3 步:发进微信(一次性装两个小工具)

```bash
brew install blackhole-2ch switchaudio-osx
# 装完 BlackHole 后重启一次微信
```

之后每次发语音就两个动作:

```bash
./scripts/mac_send_voice.sh voice.wav
```

脚本会把系统音频切到虚拟声卡并倒计时 3 秒——**倒计时内切到微信开始录音**
(按住 Fn 或点话筒图标),播放结束后停止录音、点发送。脚本退出时自动把
音频设备恢复原样。

## 常见问题

- **录进去是静音?** 重启微信再试(它可能在设备切换前就占住了旧麦克风)。
- **想边录边听到内容?** 默认为了不外放是听不到的;想监听可在「音频 MIDI
  设置」里建一个包含 BlackHole + 扬声器的「多输出设备」,脚本期间手动选它。
- **语音时长限制**:微信语音最长 60 秒,长内容分几条发。
- **为什么录音要手动按?** 自动化点击微信按钮有封号风险,虚拟声卡本身只是
  音频驱动、无法被识别为机器人——手动触发是最安全的分工。

## 该说的丑话

只用你自己的声音;别用它让人误以为"你正在亲口实时说话"来达成欺骗目的。
给熟人图个方便、图个乐没问题,冒充和诈骗是红线——这条线也保护你自己。
