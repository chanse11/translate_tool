# 实时翻译工具

Windows 桌面端中英同传，面向 Zoom / Teams 等会议：采集对方英语显示中文字幕，把你说的中文译成英文并送进虚拟麦克风。

支持两套引擎，启动后在左侧 **翻译引擎** 里选择，点 **确定** 再生效：

| 引擎 | 说明 |
|------|------|
| **阿里百炼（LiveTranslate）** | 默认。云端实时识别 + 翻译 + 合成，延迟低，可预设音色或声音复刻 |
| **本地离线（Whisper + Argos）** | 英语环回 + 中文按住说话，ASR/翻译在本地，英文靠 Edge TTS |

## 环境要求

- Windows 10/11
- Python 3.10+（开发 / 打包）
- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/)（免费）
- 百炼引擎：阿里云百炼 API Key，以及到 `dashscope.aliyuncs.com` 的网络
- 本地引擎：首次会下载 Whisper / Argos 模型；Edge TTS 合成英文时需要网络

## 会议软件音频

1. 安装 VB-Audio Virtual Cable
2. Zoom / Teams → 音频：
   - **扬声器**：你的耳机 / 音箱（正常听对方）
   - **麦克风**：`CABLE Output (VB-Audio Virtual Cable)`
3. 本工具用你的**物理麦克风**录中文；会议软件不要直接用物理麦，否则对方会听到原声中文

建议戴耳机，避免扬声器回灌进麦克风。

## 安装与运行

```powershell
cd translate_tool
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

也可指定配置文件：

```powershell
python main.py D:\path\to\config.yaml
```

开发时配置在项目根目录 `config.yaml`；打包后的 exe 会在 exe 同级生成一份可编辑的 `config.yaml`。

**不要把含 API Key 的 `config.yaml` 提交到 Git。** 分发请用 `packaging/config.yaml`（已脱敏）。

## 使用说明

### 1. 确认引擎

1. 选 **阿里百炼** 或 **本地离线**
2. 百炼需填写 **API Key**（`sk-…`），空间 ID 可选
3. 中→英发音（仅百炼）：
   - **预设音色**：如 `Ethan`（晨煦，默认男声）
   - **跟随我的声音（单人推荐）**：会话内实时复刻一次（`clone_once`）
   - **跟随当前说话人**：每句实时复刻（`clone_always`）
   - **使用已复刻音色 ID**：需先点「录制我的音色」或填已有 ID
4. 点 **确定**

### 2. 英 → 中

点 **开始英→中**：采集会议扬声器环回，字幕显示在「对方说的 · 英 → 中」。

可选 **英→中中文 TTS（耳机）**，把中文译文播到本机耳机（默认关闭）。

### 3. 中 → 英

- **持续拾取**（百炼推荐）：点 **开始中→英**，对着麦克风连续说中文，说完一句约等 1 秒静音后出英文。
- **按住说话**：按住 **Space** 或界面按钮，松手后翻译。

英文会写入 VB-Cable，会议里对方听到的是译文。勾选 **测试模式（先耳机试听）** 可同时在耳机听到相同内容。

**测试虚拟麦克风**：不说话，直接往虚拟麦送一句测试英文，用来确认 Zoom 麦克风选对了。

### 4. 其他

- **AI 助手**：点英→中条目旁的「发送」，把中文译文交给百炼对话模型（如 `qwen-plus`），右侧给出可在会上使用的回复建议。
- **快速翻译**：底部文本 / 按住说话，做一次性中英互译。
- **翻译历史**：本地 SQLite 记录，点击可回填。

## 配置

编辑 `config.yaml`（或界面里改引擎后点确定会写回）。常用项：

| 项 | 说明 |
|----|------|
| `engine.backend` | `bailian` 或 `local` |
| `audio.microphone_device` | 麦克风关键字，留空用系统默认 |
| `audio.virtual_cable_device` | 虚拟麦关键字，默认 `CABLE Input` |
| `audio.loopback_device` | 环回设备关键字，留空自动选默认扬声器 |
| `bailian.api_key` | 百炼 API Key |
| `bailian.workspace_id` | 可选业务空间 ID |
| `bailian.model` | 默认 `qwen3.5-livetranslate-flash-realtime` |
| `bailian.zh_en_voice_mode` | `preset` / `clone_once` / `clone_always` / `custom` |
| `bailian.zh_en_voice` | 预设音色名，如 `Ethan` |
| `bailian.silence_duration_ms` | 云端断句静音，默认 `1000`（过短会切半句） |
| `bailian.vad_threshold` | VAD 灵敏度，默认 `0.2` |
| `bailian.prefix_padding_ms` | 句首保留音频，默认 `500`，减轻「你/我」被切掉 |
| `bailian.stream_audio` | `false`：整句收齐再播；`true`：边收边播（更低延迟，易切半句） |
| `si.zh_input_mode` | `continuous` 持续拾取 / `ptt` 按住说话 |
| `whisper.*` | 仅本地引擎：模型名、device、beam_size |
| `tts.voice` | 本地引擎 Edge TTS 英文音色 |
| `llm.model` | AI 助手模型，默认 `qwen-plus` |

## 打包为 exe

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_win.ps1
```

或：

```powershell
pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean build.spec
```

输出：`dist/TranslateTool/TranslateTool.exe`

打包使用 `packaging/config.yaml`（不含开发机密钥）。本地引擎的 Whisper / Argos 模型不打进 exe，首次运行仍会下载。

## 目录结构

```
translate_tool/
├── main.py                 # 入口
├── config.yaml             # 开发机配置（含密钥，勿提交）
├── packaging/              # 分发用脱敏配置
├── audio/                  # 麦克风、环回、虚拟麦播放
├── asr/                    # faster-whisper（本地引擎）
├── translate/              # Argos 离线翻译（本地引擎）
├── tts/                    # Edge TTS（本地引擎）
├── bailian/                # 百炼 LiveTranslate / 对话 / 音色注册
├── pipeline/               # 本地与百炼双向流水线
├── storage/                # 翻译历史
├── ui/                     # PyQt6 界面
├── assets/                 # 图标
└── scripts/build_win.ps1   # Windows 打包
```

## 常见问题

**会议里听不到我的英文**

- 会议麦克风必须是 `CABLE Output`
- 本工具 `virtual_cable_device` 应对应 `CABLE Input`
- 先点「测试虚拟麦克风」，必要时勾选「测试模式」用耳机确认

**中文被切成半句、或句首漏字**

- 保持 `silence_duration_ms: 1000`、`vad_threshold: 0.2`、`stream_audio: false`
- 句中停顿超过约 1 秒会被当成一句结束；说快、少换气会更稳
- `prefix_padding_ms: 500` 用于保留句首轻声音节

**上一句还在出声时，下一句没反应**

- 请使用当前 master：播放已与网络收发分开，且不会在译完后把麦克风卡住
- 仍无反应时看 `logs/translate_tool.log` 是否出现 `服务端检测到语音开始`

**声音复刻仍是默认女声**

- 「跟随我的声音」要连续说几句后才会像你；复刻完成前可能是默认音色
- 持续拾取比按住说话更容易攒够复刻音频
- 「录制我的音色」走预注册，可能需要百炼声音复刻权限；实时跟随一般不必先录

**听不到中文翻译（英→中）**

- 默认只显示字幕。勾选「英→中中文 TTS」才会在耳机播放中文

**本地引擎识别慢**

- `config.yaml` 把 Whisper 改为 `base.en` / `base`
- 关掉其它占 CPU 的程序

**Edge TTS / 百炼连接失败**

- 检查网络；百炼确认 API Key、区域（默认 `cn-beijing`）

## 许可证

本项目仅供学习与个人使用。Zoom、Teams、VB-Audio、阿里云百炼、Edge TTS 等均为各自厂商产品。
