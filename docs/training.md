# 训练教程：用你的录音微调出「你的声音」

录音、导出都在本仓库完成；**训练在你自己的电脑（或云端 GPU）上进行**，
使用开源项目 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)。
它是目前中文效果最好的开源声音克隆方案之一：几分钟录音微调后，
就能用任意文本合成出接近你音色和语气的语音。

## 先想清楚：两条路线

| 路线 | 需要的数据 | 需要的硬件 | 相似度 | 适合 |
|---|---|---|---|---|
| **零样本克隆**（不训练） | 1 条 3~10 秒录音做参考 | 有 GPU 更好，CPU 也能跑（慢） | 像，但细节一般 | 先尝鲜、快速验证 |
| **微调训练**（推荐） | 5~30 分钟录音（本项目语料全录约 15~25 分钟） | NVIDIA 显卡，显存 ≥ 6GB；或云端/Colab | 明显更像、更稳定 | 认真做一个自己的 TTS |

好消息：这两条路用的是同一套录音。哪怕只录了几句，也可以先走零样本路线体验；
录完全部语料后再微调。

## 路线一：微调 GPT-SoVITS（推荐）

### 第 1 步：安装 GPT-SoVITS

- **Windows（最简单）**：从官方仓库 Releases 下载「整合包」（自带全部环境，免安装），
  解压后双击 `go-webui.bat` 即可打开 WebUI。
- **Linux / macOS**：
  ```bash
  git clone https://github.com/RVC-Boss/GPT-SoVITS.git
  cd GPT-SoVITS
  conda create -n GPTSoVits python=3.10 -y && conda activate GPTSoVits
  bash install.sh   # 或按仓库 README 手动 pip install -r requirements.txt
  python webui.py
  ```
- **没有显卡**：官方提供 Colab 笔记本，把 `dataset/` 目录上传到 Colab 同样能训练。

打开后浏览器会出现 WebUI（默认 http://127.0.0.1:9874）。

### 第 2 步：准备数据（本仓库完成）

```bash
cd voice-chat-generation
python3 recorder/server.py          # 录音（能录多少录多少，A 批优先）
python3 scripts/export_dataset.py   # 导出 dataset/gptsovits.list 并做质量体检
```

体检报告里提示削波/过短/过小声的条目，建议回录音页重录（会自动覆盖），
然后重新导出一次。

> 我们的数据是「先有文本、照着念」采集的，天然精确对齐，
> 因此 **GPT-SoVITS WebUI 里「0-前置数据集获取工具」（切割、ASR、打标）整个跳过**，
> 这正是本项目录音方式的最大优势——没有语音识别错误。

### 第 3 步：训练集格式化（WebUI 标签页 1A）

在「1-GPT-SOVITS-TTS → 1A-训练集格式化工具」中填：

| 字段 | 填什么 |
|---|---|
| 实验/模型名 | 随意，例如 `myvoice` |
| 文本标注文件 | `voice-chat-generation/dataset/gptsovits.list` 的完整路径 |
| 训练集音频文件目录 | `voice-chat-generation/dataset/wavs` 的完整路径 |

然后点 **「开启一键三连」**（文本处理 → 特征提取 → 语义 token 提取），等待完成。

### 第 4 步：微调（标签页 1B）

1. **SoVITS 训练**：batch size 按显存来（6GB 显存填 1~4），轮数默认（约 8~15 epoch）即可，点「开启 SoVITS 训练」。
2. **GPT 训练**：同样默认参数，点「开启 GPT 训练」。

十几分钟数据在消费级显卡上通常各训 10~30 分钟。

### 第 5 步：合成（标签页 1C）

1. 在下拉框选中你刚训练出的 GPT 和 SoVITS 权重，点「开启 TTS 推理 WebUI」。
2. 推理页里：
   - **参考音频**：上传一条你录得最满意的 3~10 秒录音（如 `dataset/wavs/A24.wav`），
     并把它的文本填进「参考音频的文本」（在 `dataset/meta.jsonl` 里能查到）。
     参考音频的语气会影响合成语气——想要什么情绪就选哪条。
   - **需要合成的文本**：随便输入中文，点合成试听。

### 第 6 步（可选）：命令行合成

想脱离网页批量合成，可启动 GPT-SoVITS 的 API 服务：

```bash
# 在 GPT-SoVITS 目录
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

然后回到本仓库：

```bash
python3 scripts/tts_client.py "你好，这是我自己的声音。" --ref dataset/wavs/A24.wav --out hello.wav
```

参考音频取自 `dataset/wavs/` 时，参考文本会自动从 `meta.jsonl` 查出。

## 路线二：零样本克隆（不训练，先尝鲜）

以下项目都支持"给一条参考音频 + 文本 → 直接合成"，中文表现都不错：

- **CosyVoice 2**（阿里 FunAudioLLM）：中文自然度高，3 秒参考即可。
- **F5-TTS**：安装简单，`pip install f5-tts` 后 `f5-tts_infer-cli` 一行命令合成。
- **Fish Speech / OpenAudio**：有精致的 WebUI，多语种。
- GPT-SoVITS 本身不训练也能零样本推理（1C 推理页直接给参考音频即可）。

同样用你在本项目录的任意一条干净录音当参考音频就行。

## 常见问题

- **合成出来不太像我？** 数据量加到 15 分钟以上；换一条更干净、更有代表性的参考音频；确认录音没有开系统降噪/美化。
- **有电流声或底噪？** 训练数据的底噪会被模型学走。换安静房间、离麦近一点重录，比任何后期降噪都有效。
- **数字、多音字读错？** 合成文本里尽量写汉字数字（「三点半」而非「3:30」），多音字可换写法引导。
- **能训练粤语/英语吗？** GPT-SoVITS 支持中/英/日/韩/粤，把语料换成对应语言、导出时把 `ZH` 改成对应语言代码即可（导出脚本 `--help` 查看）。

## 伦理提醒

只克隆**你自己**的声音，或已取得明确授权的声音；不要把合成语音用于冒充他人。
多数开源项目的许可证也有同样要求。
