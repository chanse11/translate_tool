"""PyQt6 主界面。"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pipeline.en_to_zh import EnToZhPipeline, SubtitleEntry
from pipeline.resources import AppResources
from pipeline.zh_to_en import TranslationResult, ZhToEnPipeline
from ui.ptt_key_filter import PttKeyFilter

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    subtitle_received = pyqtSignal(object)
    zh_en_result = pyqtSignal(object)
    preload_progress = pyqtSignal(str)
    preload_finished = pyqtSignal(bool, str)

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config
        self._ptt_active = False
        self._en_running = False
        self._zh_continuous_running = False
        self._ready = False
        self._zh_input_mode = config.get("si", {}).get("zh_input_mode", "continuous")

        self.setWindowTitle("Zoom 实时翻译")
        self.setMinimumSize(720, 640)
        self._build_ui()

        self.subtitle_received.connect(self._append_subtitle)
        self.zh_en_result.connect(self._append_zh_en)
        self.preload_progress.connect(self._on_preload_progress)
        self.preload_finished.connect(self._on_preload_finished)

        self._resources = AppResources(config)
        self._zh_pipeline = ZhToEnPipeline(
            config,
            self._resources,
            on_result=lambda r: self.zh_en_result.emit(r),
            on_status=lambda s: self._zh_status_label.setText(s),
            on_tts_start=self._on_zh_tts_start,
            on_tts_end=self._on_zh_tts_end,
            is_test_mode=lambda: self._test_mode_cb.isChecked(),
        )
        self._en_pipeline = EnToZhPipeline(
            config,
            self._resources,
            on_subtitle=lambda e: self.subtitle_received.emit(e),
            on_status=lambda s: self._en_status_label.setText(s),
            is_paused=self._is_en_capture_paused,
            tts_enabled=lambda: self._en_tts_cb.isChecked(),
        )

        self._set_controls_enabled(False)
        app = QApplication.instance()
        if app is not None and self._zh_input_mode == "ptt":
            self._ptt_filter = PttKeyFilter(self)
            app.installEventFilter(self._ptt_filter)

        if config.get("startup", {}).get("preload_models", True):
            self._start_preload()
        else:
            self._on_preload_finished(True, "已跳过预加载（config: preload_models=false）")

    def _is_en_capture_paused(self) -> bool:
        return self._zh_pipeline.is_tts_playing or self._en_pipeline.is_tts_playing

    def _on_zh_tts_start(self) -> None:
        if self._en_running:
            self._en_status_label.setText("对方英语监听暂停（TTS 播放中）…")

    def _on_zh_tts_end(self) -> None:
        if self._en_running:
            self._en_status_label.setText("英→中监听中…")

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        header = QLabel("Zoom 实时翻译工具")
        header.setFont(QFont("", 16, QFont.Weight.Bold))
        layout.addWidget(header)

        self._preload_label = QLabel("正在启动，准备加载模型…")
        self._preload_label.setStyleSheet("color: #c60; font-weight: bold;")
        self._preload_label.setWordWrap(True)
        layout.addWidget(self._preload_label)

        if self._zh_input_mode == "continuous":
            hint = QLabel(
                "Zoom 麦克风请设为 CABLE Output；扬声器正常即可。\n"
                "中→英为 VAD 连续模式：说完一句自动翻译并播英文到 Zoom。"
            )
        else:
            hint = QLabel(
                "Zoom 麦克风请设为 CABLE Output；扬声器正常即可。\n"
                "按住 Space 说中文，松手后自动翻译并播英文到 Zoom。"
            )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        layout.addWidget(hint)

        en_group = QGroupBox("对方说的（英 → 中）")
        en_layout = QVBoxLayout(en_group)
        self._en_subtitles = QTextEdit()
        self._en_subtitles.setReadOnly(True)
        self._en_subtitles.setPlaceholderText("Zoom 会议英语将在此显示中文翻译…")
        en_layout.addWidget(self._en_subtitles)
        en_opts_row = QHBoxLayout()
        en_tts_default = self._config.get("si", {}).get("en_tts_default", False)
        self._en_tts_cb = QCheckBox("中文 TTS 播报（耳机）")
        self._en_tts_cb.setChecked(en_tts_default)
        en_opts_row.addWidget(self._en_tts_cb)
        en_opts_row.addStretch()
        en_layout.addLayout(en_opts_row)
        en_btn_row = QHBoxLayout()
        self._en_toggle_btn = QPushButton("开始监听")
        self._en_toggle_btn.clicked.connect(self._toggle_en_pipeline)
        en_btn_row.addWidget(self._en_toggle_btn)
        self._en_status_label = QLabel("等待模型加载")
        self._en_status_label.setStyleSheet("color: #888;")
        en_btn_row.addWidget(self._en_status_label, stretch=1)
        en_layout.addLayout(en_btn_row)
        layout.addWidget(en_group, stretch=2)

        if self._zh_input_mode == "continuous":
            zh_title = "你说的（中 → 英，VAD 连续）"
        else:
            zh_title = "你说的（中 → 英，按住 Space）"
        zh_group = QGroupBox(zh_title)
        zh_layout = QVBoxLayout(zh_group)
        self._zh_output = QTextEdit()
        self._zh_output.setReadOnly(True)
        if self._zh_input_mode == "continuous":
            self._zh_output.setPlaceholderText("开启连续监听后，中文将自动识别并翻译…")
        else:
            self._zh_output.setPlaceholderText("按住 Space 录音，松手后显示中文与英文翻译…")
        zh_layout.addWidget(self._zh_output)
        zh_btn_row = QHBoxLayout()
        if self._zh_input_mode == "continuous":
            self._zh_toggle_btn = QPushButton("开始连续监听")
            self._zh_toggle_btn.clicked.connect(self._toggle_zh_continuous)
            zh_btn_row.addWidget(self._zh_toggle_btn)
            self._ptt_btn = None
        else:
            self._ptt_btn = QPushButton("按住说话 (Space)")
            self._ptt_btn.setCheckable(True)
            self._ptt_btn.pressed.connect(self._on_ptt_press)
            self._ptt_btn.released.connect(self._on_ptt_release)
            zh_btn_row.addWidget(self._ptt_btn)
            self._zh_toggle_btn = None
        self._zh_status_label = QLabel("等待模型加载")
        self._zh_status_label.setStyleSheet("color: #888;")
        zh_btn_row.addWidget(self._zh_status_label, stretch=1)
        zh_layout.addLayout(zh_btn_row)
        test_mode_default = self._config.get("test", {}).get("test_mode_default", False)
        self._test_mode_cb = QCheckBox("测试模式（先耳机试听，再送入 Zoom）")
        self._test_mode_cb.setChecked(test_mode_default)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._test_mode_cb)
        mode_row.addStretch()
        zh_layout.addLayout(mode_row)
        test_row = QHBoxLayout()
        self._test_vmic_btn = QPushButton("测试虚拟麦克风")
        self._test_vmic_btn.clicked.connect(self._on_test_virtual_mic)
        test_row.addWidget(self._test_vmic_btn)
        test_row.addStretch()
        zh_layout.addLayout(test_row)
        test_hint = QLabel(
            "未勾选测试模式：翻译后直接送入 Zoom。\n"
            "勾选测试模式：每句英文先在耳机播放，再送入虚拟麦克风。"
        )
        test_hint.setWordWrap(True)
        test_hint.setStyleSheet("color: #888; font-size: 11px;")
        zh_layout.addWidget(test_hint)
        layout.addWidget(zh_group, stretch=1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._en_toggle_btn.setEnabled(enabled)
        if self._ptt_btn is not None:
            self._ptt_btn.setEnabled(enabled)
        if self._zh_toggle_btn is not None:
            self._zh_toggle_btn.setEnabled(enabled)
        self._test_vmic_btn.setEnabled(enabled)

    def _start_preload(self) -> None:
        def _run() -> None:
            try:
                self._resources.preload(
                    on_progress=lambda msg: self.preload_progress.emit(msg)
                )
                self.preload_finished.emit(True, "全部就绪，可以开始使用。")
            except Exception as exc:
                self.preload_finished.emit(False, str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_preload_progress(self, msg: str) -> None:
        self._preload_label.setText(msg)

    def _on_preload_finished(self, success: bool, msg: str) -> None:
        if success:
            self._ready = True
            self._preload_label.setText("✓ " + msg)
            self._preload_label.setStyleSheet("color: #080; font-weight: bold;")
            self._en_status_label.setText("未启动")
            self._zh_status_label.setText("就绪")
            self._set_controls_enabled(True)
        else:
            self._preload_label.setText("✗ 加载失败: " + msg)
            self._preload_label.setStyleSheet("color: #c00; font-weight: bold;")
            self._en_status_label.setText("加载失败")
            self._zh_status_label.setText("加载失败")

    def _toggle_en_pipeline(self) -> None:
        if self._en_running:
            self._en_pipeline.stop()
            self._en_running = False
            self._en_toggle_btn.setText("开始监听")
        else:
            try:
                self._en_pipeline.start()
                self._en_running = True
                self._en_toggle_btn.setText("停止监听")
            except Exception as exc:
                logger.exception("启动英→中失败")
                self._en_status_label.setText(f"启动失败: {exc}")

    def _toggle_zh_continuous(self) -> None:
        if self._zh_continuous_running:
            self._zh_pipeline.stop_continuous()
            self._zh_continuous_running = False
            self._zh_toggle_btn.setText("开始连续监听")
        else:
            try:
                self._zh_pipeline.start_continuous()
                self._zh_continuous_running = True
                self._zh_toggle_btn.setText("停止连续监听")
            except Exception as exc:
                logger.exception("启动中→英连续模式失败")
                self._zh_status_label.setText(f"启动失败: {exc}")

    def _on_ptt_press(self) -> None:
        if not self._ready:
            return
        self._ptt_active = True
        if self._ptt_btn:
            self._ptt_btn.setText("录音中…")
        try:
            self._zh_pipeline.ptt_press()
        except Exception as exc:
            self._ptt_active = False
            if self._ptt_btn:
                self._ptt_btn.setText("按住说话 (Space)")
                self._ptt_btn.setChecked(False)
            self._zh_status_label.setText(f"错误: {exc}")

    def _on_test_virtual_mic(self) -> None:
        if not self._ready:
            return
        try:
            self._zh_pipeline.test_virtual_mic()
        except Exception as exc:
            self._zh_status_label.setText(f"测试失败: {exc}")

    def _on_ptt_release(self) -> None:
        if not self._ready:
            return
        self._ptt_active = False
        if self._ptt_btn:
            self._ptt_btn.setText("按住说话 (Space)")
            self._ptt_btn.setChecked(False)
        self._zh_pipeline.ptt_release()

    def _append_subtitle(self, entry: SubtitleEntry) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        block = f"[{ts}]\nEN: {entry.english}\nZH: {entry.chinese}\n"
        self._en_subtitles.append(block)
        max_lines = self._config.get("ui", {}).get("max_subtitle_lines", 50)
        if self._en_subtitles.document().blockCount() > max_lines * 3:
            cursor = self._en_subtitles.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(
                cursor.MoveOperation.Down,
                cursor.MoveMode.KeepAnchor,
                3,
            )
            cursor.removeSelectedText()

    def _append_zh_en(self, result: TranslationResult) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        block = f"[{ts}]\nZH: {result.chinese}\nEN: {result.english}\n"
        self._zh_output.append(block)

    def closeEvent(self, event) -> None:
        if self._en_running:
            self._en_pipeline.stop()
        if self._zh_continuous_running:
            self._zh_pipeline.stop_continuous()
        event.accept()


def run_app(config: dict) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(config)
    window.show()
    app.exec()
