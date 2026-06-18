# Zoom 实时翻译工具

在 Zoom 桌面版中进行中英实时对话翻译：

- **英 → 中**：采集 Zoom 播放音频，本地 ASR + 离线翻译，界面显示中文字幕
- **中 → 英**：按住 Space 说中文，本地 ASR + 离线翻译 + Edge TTS，英文播放到 VB-Cable 虚拟麦克风

## 环境要求

- Windows 10/11
- Python 3.10+（开发/打包用）
- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/)（免费）
- 网络（仅 Edge TTS 合成英文时需要；ASR 与翻译均本地）

## Zoom 音频设置

1. 安装 VB-Audio Virtual Cable
2. Zoom → 设置 → 音频：
   - **扬声器**：你的耳机/音箱（正常收听）
   - **麦克风**：`CABLE Output (VB-Audio Virtual Cable)`
3. 本工具会使用你的**物理麦克风**录中文，Zoom 不会直接使用物理麦

## 安装与运行

```powershell
cd translate_tool
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

首次运行会自动下载：

- Whisper 模型（英 `small.en`、中 `small`，可在 `config.yaml` 修改）
- Argos 离线翻译包（en↔zh）

## 使用说明

1. 启动程序，点击 **「开始监听」** 采集 Zoom 英语并显示中文翻译
2. **按住 Space**（或按住界面按钮）说中文
3. **松手** 后自动识别、翻译、TTS，英文从 VB-Cable 进入 Zoom
4. TTS 播放期间，英→中会暂停识别，避免回声
5. 点击 **「测试虚拟麦克风」**：向 VB-Cable 播放测试英文；勾选 **「耳机监听」** 可在耳机听到与送入虚拟麦相同的内容

### 虚拟麦克风测试

- **耳机监听（推荐）**：勾选后，测试音频同时送到 VB-Cable 和你的耳机，耳机里听到的就是送入虚拟麦的内容
- **仅虚拟麦**：取消勾选，只写入 VB-Cable；可在 Windows「声音 → 录制 → CABLE Output → 属性 → 侦听」里启用「侦听此设备」，用扬声器确认 Zoom 会收到的声音

## 配置

编辑 `config.yaml`：

| 项 | 说明 |
|----|------|
| `audio.loopback_device` | 环回设备关键字，留空自动选默认扬声器 |
| `audio.microphone_device` | 麦克风关键字，留空用系统默认 |
| `audio.virtual_cable_device` | 虚拟麦关键字，默认 `CABLE Input` |
| `whisper.english_model` | 英语模型，如 `base.en`、`small.en` |
| `whisper.chinese_model` | 中文模型，如 `base`、`small` |
| `tts.voice` | Edge TTS 音色，如 `en-US-JennyNeural` |

## 打包为 exe

```powershell
pip install pyinstaller
pyinstaller build.spec
```

输出目录：`dist/ZoomTranslate/ZoomTranslate.exe`

> Whisper 与 Argos 模型不在 exe 内，首次运行仍会下载到用户目录。可将模型缓存目录复制到 exe 同级以离线分发。

## 目录结构

```
translate_tool/
├── main.py              # 入口
├── config.yaml          # 配置
├── audio/               # 音频采集与播放
├── asr/                 # faster-whisper
├── translate/           # Argos 离线翻译
├── tts/                 # Edge TTS
├── pipeline/            # 双向流水线
└── ui/                  # PyQt6 界面
```

## 常见问题

**Zoom 里听不到我的英文**

- 确认 Zoom 麦克风为 `CABLE Output`
- 在 Windows 声音设置中，确认 `CABLE Input` 为默认录制设备或与本工具配置一致
- 点击程序内状态，确认 TTS 播放无报错

**听不出中文翻译**

- 本工具仅显示字幕，不播放中文语音（按设计）

**识别慢**

- 在 `config.yaml` 将 Whisper 改为 `base.en` / `base`
- 关闭其他占用 CPU 的程序

**Edge TTS 失败**

- 检查网络连接
- 可更换 `tts.voice` 为其他英文音色

## 许可证

本项目仅供学习与个人使用。Zoom、VB-Audio、Edge TTS 等均为各自厂商产品。
