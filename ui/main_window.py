"""PyQt6 主界面：左侧设置 + 中间双流式字幕 + 右侧 AI 回复。"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPalette, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from bailian.chat import (
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    chat_completion,
    chat_completion_stream,
)
from config_loader import app_dir, resource_dir, save_config
from storage.history_db import HistoryItem, TranslateHistoryStore
from ui.styles import APP_STYLESHEET

QUICK_TRANSLATE_SYSTEM_PROMPT = (
    "你是专业翻译器。只输出译文本身，不要解释、不要加引号或前后缀。"
)

# 原文 / 译文配色（与快速翻译一致）
COLOR_SOURCE = "#2563eb"
COLOR_TARGET = "#15803d"
COLOR_META = "#64748b"
# 底部「快速翻译 / 翻译历史」统一高度
BOTTOM_PANEL_HEIGHT = 220

logger = logging.getLogger(__name__)

BACKEND_LOCAL = "local"
BACKEND_BAILIAN = "bailian"


class DashedSeparator(QWidget):
    """横向灰色虚线。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("zhModeSeparator")
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor("#9ca3af"))
        pen.setWidth(1)
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 3])
        painter.setPen(pen)
        painter.drawLine(0, 0, self.width(), 0)


@dataclass
class EnSubtitleEntry:
    ts: str
    english: str
    chinese: str


class MainWindow(QMainWindow):
    subtitle_received = pyqtSignal(object)
    zh_en_result = pyqtSignal(object)
    preload_progress = pyqtSignal(str)
    preload_finished = pyqtSignal(bool, str)
    llm_stream_started = pyqtSignal(str)  # chinese
    llm_stream_delta = pyqtSignal(str)  # text chunk
    llm_stream_finished = pyqtSignal()
    llm_error = pyqtSignal(str)
    quick_translate_finished = pyqtSignal(str, str, str)  # source, target, direction
    quick_translate_error = pyqtSignal(str)
    quick_asr_finished = pyqtSignal(str)
    quick_asr_error = pyqtSignal(str)
    en_op_finished = pyqtSignal(bool, bool, str)  # starting, success, error
    zh_op_finished = pyqtSignal(bool, bool, str)  # starting, success, error
    enroll_progress = pyqtSignal(str)
    enroll_finished = pyqtSignal(str, str)  # voice_id, error

    def __init__(self, config: dict, config_path: Path | None = None) -> None:
        super().__init__()
        self._config = config
        self._config_path = config_path
        self._ptt_active = False
        self._en_running = False
        self._zh_continuous_running = False
        self._en_busy = False
        self._zh_busy = False
        self._ready = False
        self._local_preloaded = False
        self._zh_input_mode = config.get("si", {}).get("zh_input_mode", "continuous")
        self._backend = config.get("engine", {}).get("backend", BACKEND_LOCAL)
        if self._backend not in (BACKEND_LOCAL, BACKEND_BAILIAN):
            self._backend = BACKEND_LOCAL
        self._zh_audio_warning = ""
        self._en_entries: list[EnSubtitleEntry] = []
        self._en_row_widgets: list[QWidget] = []
        self._zh_history_blocks: list[str] = []
        self._en_partial_text = ""
        self._zh_partial_text = ""
        self._llm_busy = False
        self._llm_active_button: QPushButton | None = None
        self._llm_reply_blocks: list[str] = []
        self._llm_streaming = False
        self._llm_stream_ts = ""
        self._llm_stream_question = ""
        self._llm_stream_answer = ""
        self._quick_busy = False
        self._quick_mic_recorder = None
        self._quick_last_translation = ""
        self._history_store = TranslateHistoryStore(
            app_dir() / "data" / "translate_history.db"
        )

        self.setWindowTitle("实时翻译工具")
        self.setMinimumSize(1100, 700)
        self.setStyleSheet(APP_STYLESHEET)
        icon_path = resource_dir() / "assets" / "app_icon.ico"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()

        self.subtitle_received.connect(self._append_subtitle)
        self.zh_en_result.connect(self._append_zh_en)
        self.preload_progress.connect(self._on_preload_progress)
        self.preload_finished.connect(self._on_preload_finished)
        self.llm_stream_started.connect(self._on_llm_stream_started)
        self.llm_stream_delta.connect(self._on_llm_stream_delta)
        self.llm_stream_finished.connect(self._on_llm_stream_finished)
        self.llm_error.connect(self._on_llm_error)
        self.quick_translate_finished.connect(self._on_quick_translate_finished)
        self.quick_translate_error.connect(self._on_quick_translate_error)
        self.quick_asr_finished.connect(self._on_quick_asr_finished)
        self.quick_asr_error.connect(self._on_quick_asr_error)
        self.en_op_finished.connect(self._on_en_op_finished)
        self.zh_op_finished.connect(self._on_zh_op_finished)
        self.enroll_progress.connect(self._on_enroll_progress)
        self.enroll_finished.connect(self._on_enroll_done)

        self._resources = None
        self._zh_pipeline = None
        self._en_pipeline = None
        self._engine_confirmed = False
        self._loading_local = False

        self._set_controls_enabled(False)
        self._ptt_filter = None
        app = QApplication.instance()
        if app is not None:
            from ui.ptt_key_filter import PttKeyFilter

            self._ptt_filter = PttKeyFilter(self)
            app.installEventFilter(self._ptt_filter)

        self._sync_backend_ui()
        self._show_awaiting_confirm()
        self._sync_backend_ui()
        self._update_stream_placeholders()
        self._sync_voice_mode_ui()
        self._set_zh_live_warning(False)
        self._load_translate_history()

    def _bailian_api_key(self) -> str:
        return self._api_key_edit.text().strip()

    def _is_en_capture_paused(self) -> bool:
        if self._zh_pipeline is None or self._en_pipeline is None:
            return False
        return self._zh_pipeline.is_tts_playing or self._en_pipeline.is_tts_playing

    def _on_zh_tts_start(self) -> None:
        if self._en_running:
            self._en_status_label.setText("英→中暂停（TTS 播放中）")

    def _on_zh_tts_end(self) -> None:
        if self._en_running:
            mode = "流式" if self._backend == BACKEND_BAILIAN else "监听"
            self._en_status_label.setText(f"英→中{mode}中")

    def _ensure_local_resources(self) -> None:
        if self._resources is None:
            from pipeline.factory import create_app_resources

            self._resources = create_app_resources(self._config)

    def _rebuild_pipelines(self) -> None:
        from pipeline.factory import (
            create_en_to_zh_pipeline,
            create_zh_to_en_pipeline,
        )

        api_key = self._bailian_api_key()
        if self._backend == BACKEND_LOCAL:
            self._ensure_local_resources()

        self._zh_pipeline = create_zh_to_en_pipeline(
            self._backend,
            self._config,
            resources=self._resources,
            api_key=api_key,
            on_result=lambda r: self.zh_en_result.emit(r),
            on_status=lambda s: self._zh_status_label.setText(s),
            on_tts_start=self._on_zh_tts_start,
            on_tts_end=self._on_zh_tts_end,
            is_test_mode=lambda: self._test_mode_cb.isChecked(),
        )
        self._en_pipeline = create_en_to_zh_pipeline(
            self._backend,
            self._config,
            resources=self._resources,
            api_key=api_key,
            on_subtitle=lambda e: self.subtitle_received.emit(e),
            on_status=lambda s: self._en_status_label.setText(s),
            is_paused=self._is_en_capture_paused,
            tts_enabled=lambda: self._en_tts_cb.isChecked(),
        )

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        outer = QHBoxLayout(root)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)

        outer.addWidget(self._build_settings_panel(), 0)
        outer.addWidget(self._build_content_panel(), 1)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _build_settings_panel(self) -> QWidget:
        outer = QWidget()
        outer.setObjectName("settingsPanel")
        outer.setFixedWidth(340)
        outer.setMinimumWidth(340)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        panel = QWidget()
        panel.setObjectName("settingsInner")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self._preload_label = QLabel("")
        self._preload_label.setObjectName("preloadLabel")
        self._preload_label.setWordWrap(True)
        self._preload_label.hide()
        layout.addWidget(self._preload_label)

        layout.addWidget(self._build_engine_group())
        layout.addWidget(self._build_llm_group())

        audio_hint = QLabel(
            "会议软件麦克风：CABLE Output\n扬声器：你的耳机/音箱"
        )
        audio_hint.setWordWrap(True)
        audio_hint.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(audio_hint)

        ctrl = QGroupBox("监听控制")
        ctrl_layout = QVBoxLayout(ctrl)
        ctrl_layout.setSpacing(8)

        self._en_toggle_btn = QPushButton("开始英→中")
        self._en_toggle_btn.setMinimumHeight(30)
        self._en_toggle_btn.clicked.connect(self._toggle_en_pipeline)
        ctrl_layout.addWidget(self._en_toggle_btn)

        self._en_status_label = QLabel("等待就绪")
        self._en_status_label.setObjectName("statusLabel")
        ctrl_layout.addWidget(self._en_status_label)

        en_tts_default = self._config.get("si", {}).get("en_tts_default", False)
        self._en_tts_cb = QCheckBox("英→中中文 TTS（耳机）")
        self._en_tts_cb.setChecked(en_tts_default)
        ctrl_layout.addWidget(self._en_tts_cb)

        sep_wrap = QWidget()
        sep_layout = QVBoxLayout(sep_wrap)
        sep_layout.setContentsMargins(0, 5, 0, 5)
        sep_layout.setSpacing(0)
        sep_layout.addWidget(DashedSeparator())
        ctrl_layout.addWidget(sep_wrap)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        self._zh_mode_group = QButtonGroup(self)
        self._zh_continuous_radio = QRadioButton("持续拾取")
        self._zh_ptt_radio = QRadioButton("按住说话")
        self._zh_mode_group.addButton(self._zh_continuous_radio)
        self._zh_mode_group.addButton(self._zh_ptt_radio)
        if self._zh_input_mode == "ptt":
            self._zh_ptt_radio.setChecked(True)
        else:
            self._zh_continuous_radio.setChecked(True)
        self._zh_continuous_radio.toggled.connect(self._on_zh_input_mode_changed)
        self._zh_ptt_radio.toggled.connect(self._on_zh_input_mode_changed)
        mode_row.addWidget(self._zh_continuous_radio)
        mode_row.addWidget(self._zh_ptt_radio)
        mode_row.addStretch()
        ctrl_layout.addLayout(mode_row)

        self._zh_mode_hint = QLabel("")
        self._zh_mode_hint.setWordWrap(True)
        self._zh_mode_hint.setStyleSheet("color: #64748b; font-size: 11px;")
        ctrl_layout.addWidget(self._zh_mode_hint)

        self._zh_toggle_btn = QPushButton("开始中→英")
        self._zh_toggle_btn.setMinimumHeight(30)
        self._zh_toggle_btn.clicked.connect(self._toggle_zh_continuous)
        ctrl_layout.addWidget(self._zh_toggle_btn)

        self._ptt_btn = QPushButton("按住说话 (Space)")
        self._ptt_btn.setMinimumHeight(30)
        self._ptt_btn.setCheckable(True)
        self._ptt_btn.pressed.connect(self._on_ptt_press)
        self._ptt_btn.released.connect(self._on_ptt_release)
        ctrl_layout.addWidget(self._ptt_btn)

        self._zh_status_label = QLabel("等待就绪")
        self._zh_status_label.setObjectName("statusLabel")
        ctrl_layout.addWidget(self._zh_status_label)

        test_mode_default = self._config.get("test", {}).get("test_mode_default", False)
        self._test_mode_cb = QCheckBox("测试模式（先耳机试听）")
        self._test_mode_cb.setChecked(test_mode_default)
        ctrl_layout.addWidget(self._test_mode_cb)

        self._test_vmic_btn = QPushButton("测试虚拟麦克风")
        self._test_vmic_btn.setObjectName("secondaryBtn")
        self._test_vmic_btn.setMinimumHeight(30)
        self._test_vmic_btn.clicked.connect(self._on_test_virtual_mic)
        ctrl_layout.addWidget(self._test_vmic_btn)

        layout.addWidget(ctrl)
        layout.addStretch()

        scroll.setWidget(panel)
        outer_layout.addWidget(scroll)
        self._sync_zh_input_mode_ui()
        return outer

    def _build_llm_group(self) -> QGroupBox:
        group = QGroupBox("AI 助手")
        layout = QVBoxLayout(group)
        llm_cfg = self._config.get("llm", {}) or {}

        form = QFormLayout()
        self._llm_model_edit = QLineEdit()
        self._llm_model_edit.setPlaceholderText("例如 qwen-plus")
        self._llm_model_edit.setFixedHeight(28)
        self._llm_model_edit.setText(
            (llm_cfg.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        )
        form.addRow("模型", self._llm_model_edit)
        layout.addLayout(form)

        prompt_label = QLabel("System Prompt")
        prompt_label.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(prompt_label)

        self._llm_prompt_edit = QTextEdit()
        self._llm_prompt_edit.setObjectName("llmPromptEdit")
        self._llm_prompt_edit.setPlaceholderText("可自定义发给大模型的系统提示…")
        self._llm_prompt_edit.setFixedHeight(62)
        saved_prompt = (llm_cfg.get("system_prompt") or "").strip()
        self._llm_prompt_edit.setPlainText(saved_prompt or DEFAULT_SYSTEM_PROMPT)
        layout.addWidget(self._llm_prompt_edit)

        tip = QLabel("点英→中条目旁「发送」，将中文译文交给大模型。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #64748b; font-size: 11px;")
        layout.addWidget(tip)
        return group

    def _build_content_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("contentPanel")
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        top = QWidget()
        layout = QHBoxLayout(top)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        streams = QWidget()
        streams_layout = QVBoxLayout(streams)
        streams_layout.setContentsMargins(0, 0, 0, 0)
        streams_layout.setSpacing(12)

        self._en_card = self._build_en_stream_card()
        self._zh_card, self._zh_live = self._build_stream_card(
            "你说的 · 中 → 英",
            live_object_name="liveStreamZh",
        )
        streams_layout.addWidget(self._en_card, stretch=1)
        streams_layout.addWidget(self._zh_card, stretch=1)

        layout.addWidget(streams, stretch=1)
        layout.addWidget(self._build_llm_reply_panel(), 0)

        bottom = QWidget()
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(12)
        bottom_layout.addWidget(self._build_quick_translate_panel(), stretch=1)
        bottom_layout.addWidget(self._build_history_panel(), stretch=0)

        outer.addWidget(top, stretch=1)
        outer.addWidget(bottom, stretch=0)
        return panel

    def _build_history_panel(self) -> QWidget:
        card = QWidget()
        card.setObjectName("streamCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setFixedWidth(300)
        card.setFixedHeight(BOTTOM_PANEL_HEIGHT)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        header = QLabel("翻译历史")
        header.setObjectName("streamTitle")
        header_row.addWidget(header)
        header_row.addStretch()
        self._history_clear_btn = QPushButton("清空")
        self._history_clear_btn.setObjectName("secondaryBtn")
        self._history_clear_btn.setMinimumHeight(26)
        self._history_clear_btn.setMaximumHeight(28)
        self._history_clear_btn.clicked.connect(self._on_clear_history)
        header_row.addWidget(self._history_clear_btn)
        layout.addLayout(header_row)

        self._history_list = QListWidget()
        self._history_list.setObjectName("historyList")
        self._history_list.setWordWrap(False)
        self._history_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._history_list.setSpacing(2)
        self._history_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # Windows 默认选中文字为白色，强制保持深色可读
        hist_pal = self._history_list.palette()
        hist_pal.setColor(QPalette.ColorRole.Text, QColor("#1e293b"))
        hist_pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#1e293b"))
        hist_pal.setColor(QPalette.ColorRole.Highlight, QColor("#dbeafe"))
        self._history_list.setPalette(hist_pal)
        self._history_list.itemClicked.connect(self._on_history_item_clicked)
        layout.addWidget(self._history_list, stretch=1)

        tip = QLabel("点击条目可回填到左侧结果区")
        tip.setObjectName("statusLabel")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        return card

    def _build_quick_translate_panel(self) -> QWidget:
        card = QWidget()
        card.setObjectName("quickTranslateCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setFixedHeight(BOTTOM_PANEL_HEIGHT)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        header = QLabel("快速翻译")
        header.setObjectName("quickTranslateTitle")
        header_row.addWidget(header)
        header_row.addStretch()
        self._quick_status_label = QLabel("")
        self._quick_status_label.setObjectName("statusLabel")
        header_row.addWidget(self._quick_status_label)
        layout.addLayout(header_row)

        self._quick_result = QTextEdit()
        self._quick_result.setObjectName("quickTranslateResult")
        self._quick_result.setReadOnly(True)
        self._quick_result.setPlaceholderText("原文与译文将显示在这里…")
        self._quick_result.setMinimumHeight(80)
        layout.addWidget(self._quick_result, stretch=1)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._quick_input = QLineEdit()
        self._quick_input.setPlaceholderText("输入中文或英文，回车翻译…")
        self._quick_input.setFixedHeight(30)
        self._quick_input.returnPressed.connect(self._on_quick_translate_clicked)
        input_row.addWidget(self._quick_input, stretch=1)

        self._quick_translate_btn = QPushButton("翻译")
        self._quick_translate_btn.setMinimumHeight(30)
        self._quick_translate_btn.setMaximumHeight(32)
        self._quick_translate_btn.clicked.connect(self._on_quick_translate_clicked)
        input_row.addWidget(self._quick_translate_btn)

        self._quick_mic_btn = QPushButton("按住说话")
        self._quick_mic_btn.setObjectName("quickMicBtn")
        self._quick_mic_btn.setMinimumHeight(30)
        self._quick_mic_btn.setMaximumHeight(32)
        self._quick_mic_btn.setCheckable(True)
        self._quick_mic_btn.pressed.connect(self._on_quick_mic_press)
        self._quick_mic_btn.released.connect(self._on_quick_mic_release)
        input_row.addWidget(self._quick_mic_btn)

        self._quick_copy_btn = QPushButton("复制译文")
        self._quick_copy_btn.setObjectName("secondaryBtn")
        self._quick_copy_btn.setMinimumHeight(30)
        self._quick_copy_btn.setMaximumHeight(32)
        self._quick_copy_btn.clicked.connect(self._on_quick_copy)
        input_row.addWidget(self._quick_copy_btn)
        layout.addLayout(input_row)

        self._set_quick_controls_enabled(False)
        return card

    def _build_llm_reply_panel(self) -> QWidget:
        card = QWidget()
        card.setObjectName("streamCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setFixedWidth(300)
        card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 18)
        layout.setSpacing(8)

        header = QLabel("AI 回复")
        header.setObjectName("streamTitle")
        layout.addWidget(header)

        self._llm_reply_live = QTextEdit()
        self._llm_reply_live.setObjectName("llmReplyStream")
        self._llm_reply_live.setReadOnly(True)
        self._llm_reply_live.setPlaceholderText("点击英→中条目旁「发送」后，回复显示在这里…")
        layout.addWidget(self._llm_reply_live, stretch=1)

        self._llm_status_label = QLabel("")
        self._llm_status_label.setObjectName("statusLabel")
        self._llm_status_label.setWordWrap(True)
        layout.addWidget(self._llm_status_label)
        return card

    def _build_en_stream_card(self) -> QWidget:
        card = QWidget()
        card.setObjectName("streamCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 18)
        layout.setSpacing(8)

        header = QLabel("对方说的 · 英 → 中")
        header.setObjectName("streamTitle")
        layout.addWidget(header)

        self._en_scroll = QScrollArea()
        self._en_scroll.setObjectName("enScrollArea")
        self._en_scroll.setWidgetResizable(True)
        self._en_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._en_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self._en_list_host = QWidget()
        self._en_list_layout = QVBoxLayout(self._en_list_host)
        self._en_list_layout.setContentsMargins(8, 8, 8, 8)
        self._en_list_layout.setSpacing(8)
        self._en_list_layout.addStretch(1)

        self._en_partial_label = QLabel("")
        self._en_partial_label.setObjectName("enPartialLabel")
        self._en_partial_label.setWordWrap(True)
        self._en_partial_label.setVisible(False)
        self._en_list_layout.addWidget(self._en_partial_label)

        self._en_scroll.setWidget(self._en_list_host)
        layout.addWidget(self._en_scroll, stretch=1)
        # 兼容旧逻辑里对 _en_live 的 clear 调用：用空壳占位
        self._en_live = self._en_scroll
        return card

    def _build_stream_card(
        self, title: str, live_object_name: str
    ) -> tuple[QWidget, QTextEdit]:
        card = QWidget()
        card.setObjectName("streamCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 18)
        layout.setSpacing(8)

        header = QLabel(title)
        header.setObjectName("streamTitle")
        layout.addWidget(header)

        stream = QTextEdit()
        stream.setObjectName(live_object_name)
        stream.setReadOnly(True)
        stream.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        stream.setPlaceholderText("已完成句子将显示在这里…")
        layout.addWidget(stream, stretch=1)

        return card, stream

    def _build_engine_group(self) -> QGroupBox:
        group = QGroupBox("翻译引擎")
        layout = QVBoxLayout(group)

        self._backend_group = QButtonGroup(self)
        self._local_radio = QRadioButton("本地离线（Whisper + Argos）")
        self._bailian_radio = QRadioButton("阿里百炼（LiveTranslate）")
        self._backend_group.addButton(self._local_radio)
        self._backend_group.addButton(self._bailian_radio)
        if self._backend == BACKEND_BAILIAN:
            self._bailian_radio.setChecked(True)
        else:
            self._local_radio.setChecked(True)
        self._local_radio.toggled.connect(self._on_backend_radio_toggled)
        self._bailian_radio.toggled.connect(self._on_backend_radio_toggled)
        layout.addWidget(self._local_radio)
        layout.addWidget(self._bailian_radio)

        form = QFormLayout()
        bailian_cfg = self._config.get("bailian", {})
        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("百炼 API Key（sk-…）")
        self._api_key_edit.setFixedHeight(28)
        self._api_key_edit.setText(bailian_cfg.get("api_key", "") or "")
        form.addRow("API Key", self._api_key_edit)

        self._workspace_edit = QLineEdit()
        self._workspace_edit.setPlaceholderText("可选业务空间 ID")
        self._workspace_edit.setFixedHeight(28)
        self._workspace_edit.setText(bailian_cfg.get("workspace_id", "") or "")
        form.addRow("空间 ID", self._workspace_edit)

        self._voice_mode_combo = QComboBox()
        self._voice_mode_combo.setFixedHeight(28)
        self._voice_mode_combo.addItem("预设音色", "preset")
        self._voice_mode_combo.addItem("跟随我的声音（单人推荐）", "clone_once")
        self._voice_mode_combo.addItem("跟随当前说话人（实时复刻）", "clone_always")
        self._voice_mode_combo.addItem("使用已复刻音色 ID", "custom")
        saved_voice_mode = bailian_cfg.get("zh_en_voice_mode", "preset")
        for i in range(self._voice_mode_combo.count()):
            if self._voice_mode_combo.itemData(i) == saved_voice_mode:
                self._voice_mode_combo.setCurrentIndex(i)
                break
        self._voice_mode_combo.currentIndexChanged.connect(self._sync_voice_mode_ui)
        form.addRow("中→英发音", self._voice_mode_combo)

        self._voice_value_edit = QLineEdit()
        self._voice_value_edit.setFixedHeight(28)
        custom_id = bailian_cfg.get("zh_en_custom_voice_id", "") or ""
        if saved_voice_mode == "custom" and custom_id:
            self._voice_value_edit.setText(custom_id)
        else:
            self._voice_value_edit.setText(bailian_cfg.get("zh_en_voice", "Ethan") or "Ethan")
        form.addRow("音色 / ID", self._voice_value_edit)

        self._enroll_voice_btn = QPushButton("录制我的音色（约10秒）")
        self._enroll_voice_btn.setMinimumHeight(28)
        self._enroll_voice_btn.setToolTip(
            "可选：预注册固定音色（需声音复刻权限）。"
            "「跟随我的声音」默认用实时复刻，不必先录制。"
        )
        self._enroll_voice_btn.clicked.connect(self._on_enroll_my_voice)
        form.addRow("", self._enroll_voice_btn)
        layout.addLayout(form)

        self._confirm_engine_btn = QPushButton("确定")
        self._confirm_engine_btn.setDefault(True)
        self._confirm_engine_btn.setMinimumHeight(30)
        self._confirm_engine_btn.setMaximumHeight(32)
        self._confirm_engine_btn.clicked.connect(self._confirm_engine)
        layout.addWidget(self._confirm_engine_btn)
        return group

    def _clear_en_entries(self) -> None:
        for row in self._en_row_widgets:
            self._en_list_layout.removeWidget(row)
            row.deleteLater()
        self._en_row_widgets.clear()
        self._en_entries.clear()
        self._en_partial_text = ""
        self._en_partial_label.setText("")
        self._en_partial_label.setVisible(False)

    def _trim_en_entries(self) -> None:
        max_lines = self._config.get("ui", {}).get("max_subtitle_lines", 50)
        while len(self._en_entries) > max_lines and self._en_row_widgets:
            self._en_entries.pop(0)
            row = self._en_row_widgets.pop(0)
            self._en_list_layout.removeWidget(row)
            row.deleteLater()

    def _format_bilingual_html(
        self,
        *,
        source_label: str,
        source: str,
        target_label: str,
        target: str,
        ts: str = "",
        warning: str = "",
    ) -> str:
        parts: list[str] = []
        if ts:
            parts.append(
                f'<span style="color:{COLOR_META};font-size:12px;">'
                f"[{escape(ts)}]</span><br>"
            )
        parts.append(
            f'<span style="color:{COLOR_SOURCE};">'
            f"{escape(source_label)}: {escape(source)}</span><br>"
        )
        parts.append(
            f'<span style="color:{COLOR_TARGET};font-weight:600;">'
            f"{escape(target_label)}: {escape(target)}</span>"
        )
        if warning:
            parts.append(
                f'<br><span style="color:#b91c1c;font-weight:700;">'
                f"!! {escape(warning)}</span>"
            )
        return "".join(parts)

    def _add_en_entry_row(self, entry: EnSubtitleEntry) -> None:
        self._en_entries.append(entry)
        row = QFrame()
        row.setObjectName("enEntryRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 8, 10, 8)
        row_layout.setSpacing(8)

        text = QLabel(
            self._format_bilingual_html(
                source_label="EN",
                source=entry.english,
                target_label="ZH",
                target=entry.chinese,
                ts=entry.ts,
            )
        )
        text.setObjectName("enEntryText")
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row_layout.addWidget(text, stretch=1)

        send_btn = QPushButton("发送")
        send_btn.setObjectName("sendLlmBtn")
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        chinese = entry.chinese
        send_btn.clicked.connect(lambda _=False, c=chinese, b=send_btn: self._send_to_llm(c, b))
        row_layout.addWidget(send_btn, stretch=0, alignment=Qt.AlignmentFlag.AlignTop)

        # 插在 stretch 与 partial 标签之前
        insert_at = self._en_list_layout.count() - 2
        if insert_at < 0:
            insert_at = 0
        self._en_list_layout.insertWidget(insert_at, row)
        self._en_row_widgets.append(row)
        self._trim_en_entries()
        self._scroll_en_to_bottom()

    def _scroll_en_to_bottom(self) -> None:
        bar = self._en_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _update_en_partial(self) -> None:
        if self._en_partial_text.strip():
            self._en_partial_label.setTextFormat(Qt.TextFormat.RichText)
            self._en_partial_label.setText(self._en_partial_text)
            self._en_partial_label.setVisible(True)
        else:
            self._en_partial_label.setText("")
            self._en_partial_label.setVisible(False)
        self._scroll_en_to_bottom()

    def _update_stream_placeholders(self) -> None:
        self._update_en_partial()
        self._set_zh_live_warning(bool(self._zh_audio_warning))
        partial = self._zh_audio_warning or self._zh_partial_text
        self._render_stream(
            self._zh_live,
            self._zh_history_blocks,
            partial,
            partial_is_html=bool(self._zh_partial_text) and not self._zh_audio_warning,
        )

    def _set_zh_live_warning(self, warning: bool) -> None:
        if warning:
            self._zh_live.setStyleSheet(
                "background-color: #fef2f2;"
                "border: 1px solid #fca5a5;"
                "border-radius: 12px;"
                "padding: 10px;"
                "font-size: 14px;"
                "color: #b91c1c;"
                "font-weight: 700;"
            )
        else:
            self._zh_live.setStyleSheet("")

    def _trim_stream_blocks(self, blocks: list[str]) -> None:
        max_lines = self._config.get("ui", {}).get("max_subtitle_lines", 50)
        while len(blocks) > max_lines:
            blocks.pop(0)

    def _render_stream(
        self,
        widget: QTextEdit,
        history_blocks: list[str],
        partial_text: str = "",
        *,
        partial_is_html: bool = False,
    ) -> None:
        history_html = []
        for block in history_blocks:
            if not block.strip():
                continue
            # history 已存双语 HTML
            history_html.append(
                "<div style='margin:0 0 12px 0; font-size:13px; "
                f"line-height:1.55;'>{block}</div>"
            )

        current_html = ""
        if partial_text.strip():
            if partial_is_html:
                body = partial_text.strip()
            else:
                body = escape(partial_text.strip())
            current_html = (
                "<div style='margin-top:8px; font-size:15px; "
                f"font-weight:700; line-height:1.6;'>{body}</div>"
            )

        widget.setHtml("".join(history_html) + current_html)
        cursor = widget.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        widget.setTextCursor(cursor)

    def _render_llm_replies(self) -> None:
        history_html = []
        for block in self._llm_reply_blocks:
            if not block.strip():
                continue
            history_html.append(
                "<div style='margin:0 0 14px 0; color:#4c1d95; font-size:12px; "
                "line-height:1.55; white-space:pre-wrap;'>"
                f"{escape(block.strip())}</div>"
            )
        self._llm_reply_live.setHtml("".join(history_html))
        cursor = self._llm_reply_live.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._llm_reply_live.setTextCursor(cursor)

    def _selected_voice_mode(self) -> str:
        return self._voice_mode_combo.currentData() or "preset"

    def _sync_voice_mode_ui(self) -> None:
        mode = self._selected_voice_mode()
        custom_voice_id = (
            self._config.get("bailian", {}).get("zh_en_custom_voice_id", "") or ""
        )
        if mode == "preset":
            self._voice_value_edit.setEnabled(True)
            self._voice_value_edit.setReadOnly(False)
            self._voice_value_edit.setPlaceholderText("例如 Ethan")
            if not self._voice_value_edit.text().strip():
                self._voice_value_edit.setText("Ethan")
        elif mode == "custom":
            self._voice_value_edit.setEnabled(True)
            self._voice_value_edit.setReadOnly(False)
            self._voice_value_edit.setPlaceholderText("输入已复刻音色 ID")
            if custom_voice_id and self._voice_value_edit.text().strip() in ("", "Ethan", "Tina"):
                self._voice_value_edit.setText(custom_voice_id)
        elif mode == "clone_once":
            self._voice_value_edit.setEnabled(False)
            self._voice_value_edit.setReadOnly(True)
            self._voice_value_edit.clear()
            self._voice_value_edit.setPlaceholderText(
                "会话开始时复刻一次（与桌面版相同，无需先录制）"
            )
        else:
            # clone_always
            self._voice_value_edit.setEnabled(False)
            self._voice_value_edit.setReadOnly(True)
            self._voice_value_edit.clear()
            self._voice_value_edit.setPlaceholderText(
                "每句实时复刻当前说话人（无需先录制）"
            )
        if hasattr(self, "_enroll_voice_btn"):
            self._enroll_voice_btn.setVisible(mode == "custom")
            self._enroll_voice_btn.setEnabled(not self._pipelines_busy() and not self._loading_local)

    def _selected_backend_from_ui(self) -> str:
        return BACKEND_BAILIAN if self._bailian_radio.isChecked() else BACKEND_LOCAL

    def _sync_backend_ui(self) -> None:
        busy = self._loading_local or self._pipelines_busy()
        self._api_key_edit.setEnabled(not busy)
        self._workspace_edit.setEnabled(not busy)
        self._api_key_edit.setReadOnly(busy)
        self._workspace_edit.setReadOnly(busy)
        self._confirm_engine_btn.setEnabled(not busy)
        self._voice_mode_combo.setEnabled(not busy)
        self._voice_value_edit.setEnabled(not busy)
        if not busy:
            self._sync_voice_mode_ui()

    def _set_preload_status(self, text: str, color: str = "#64748b") -> None:
        if text:
            self._preload_label.setText(text)
            self._preload_label.setStyleSheet(f"color: {color};")
            self._preload_label.show()
        else:
            self._preload_label.clear()
            self._preload_label.hide()

    def _show_awaiting_confirm(self) -> None:
        self._ready = False
        self._engine_confirmed = False
        self._set_controls_enabled(False)
        self._set_preload_status("")
        self._en_status_label.setText("等待选择引擎")
        self._zh_status_label.setText("等待选择引擎")

    def _pipelines_busy(self) -> bool:
        return (
            self._en_running
            or self._zh_continuous_running
            or self._ptt_active
            or self._en_busy
            or self._zh_busy
        )

    def _on_backend_radio_toggled(self, checked: bool) -> None:
        if not checked:
            return
        if self._pipelines_busy() or self._loading_local:
            QMessageBox.warning(
                self, "无法切换", "请先停止监听，或等待本地模型加载完成。"
            )
            restore = self._backend if self._engine_confirmed else BACKEND_LOCAL
            if restore == BACKEND_BAILIAN:
                self._bailian_radio.blockSignals(True)
                self._bailian_radio.setChecked(True)
                self._bailian_radio.blockSignals(False)
            else:
                self._local_radio.blockSignals(True)
                self._local_radio.setChecked(True)
                self._local_radio.blockSignals(False)
            return

        if self._engine_confirmed and self._selected_backend_from_ui() != self._backend:
            self._ready = False
            self._engine_confirmed = False
            self._set_controls_enabled(False)
            self._set_preload_status("引擎已更改，请点击「确定」。", "#b45309")
            self._en_status_label.setText("等待确认")
            self._zh_status_label.setText("等待确认")
        self._sync_backend_ui()
        self._update_stream_placeholders()

    def _persist_engine_to_config(self, save_file: bool = True) -> None:
        self._config.setdefault("engine", {})["backend"] = self._backend
        bailian = self._config.setdefault("bailian", {})
        bailian["api_key"] = self._api_key_edit.text().strip()
        bailian["workspace_id"] = self._workspace_edit.text().strip()
        voice_mode = self._selected_voice_mode()
        bailian["zh_en_voice_mode"] = voice_mode
        value = self._voice_value_edit.text().strip()
        if voice_mode == "custom":
            bailian["zh_en_custom_voice_id"] = value
        elif voice_mode == "preset":
            bailian["zh_en_voice"] = value or "Ethan"
        elif voice_mode == "clone_once":
            if value.startswith("qwen-") or value.startswith("qwen"):
                bailian["zh_en_custom_voice_id"] = value
        # clone_always 不改 custom id

        llm = self._config.setdefault("llm", {})
        llm["enabled"] = True
        llm["model"] = self._llm_model_edit.text().strip() or DEFAULT_MODEL
        llm["system_prompt"] = (
            self._llm_prompt_edit.toPlainText().strip() or DEFAULT_SYSTEM_PROMPT
        )
        self._config.setdefault("si", {})["zh_input_mode"] = self._zh_input_mode

        if save_file and self._config_path is not None:
            try:
                save_config(self._config, self._config_path)
            except Exception as exc:
                logger.exception("保存配置失败")
                QMessageBox.warning(self, "保存失败", str(exc))

    def _on_enroll_my_voice(self) -> None:
        """录制约 10 秒中文，调用百炼预注册音色（frequency=never）。"""
        if self._pipelines_busy() or self._loading_local:
            QMessageBox.warning(self, "忙碌中", "请先停止监听后再录制音色。")
            return
        api_key = self._bailian_api_key()
        if not api_key:
            QMessageBox.warning(self, "缺少 API Key", "请先填写阿里百炼 API Key。")
            return
        reply = QMessageBox.question(
            self,
            "录制我的音色",
            "将倒计时 3 秒后连续录音约 12 秒，用于创建固定音色。\n\n"
            "请对着你的插孔/USB 麦克风（不要对着 VB-Cable）用正常音量朗读：\n"
            "「大家好，我正在测试声音复刻。"
            "今天会议讨论项目进度、风险和下一步安排。"
            "请保持语速自然，连续说满十秒，不要只说你好。」\n\n"
            "中间尽量不要停，环境尽量安静。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._enroll_voice_btn.setEnabled(False)
        self._set_preload_status("准备录音…", "#2563eb")

        def _run() -> None:
            err = ""
            voice_id = ""
            try:
                import time as _time

                import numpy as np
                import sounddevice as sd

                from audio.devices import resolve_input_device
                from audio.mic_stream import MicStreamCapture
                from bailian.voice_enroll import (
                    create_livetranslate_voice,
                    float32_to_wav_bytes,
                    normalize_speech,
                    resample_audio,
                    speech_window_rms,
                )

                audio_cfg = self._config.get("audio", {})
                capture_sr = int(audio_cfg.get("sample_rate", 16000) or 16000)
                enroll_sr = 24000  # 官方声音复刻要求采样率 ≥ 24 kHz
                device = resolve_input_device(audio_cfg.get("microphone_device", "") or "")
                try:
                    info = sd.query_devices(device)
                    mic_name = info.get("name", str(device)) if isinstance(info, dict) else str(info)
                except Exception:
                    mic_name = str(device)
                logger.info("音色录制使用麦克风: device=%s name=%s", device, mic_name)

                for sec in (3, 2, 1):
                    self.enroll_progress.emit(f"即将开始：{sec}… 请准备说话")
                    _time.sleep(1.0)

                chunks: list[np.ndarray] = []

                def _on_chunk(chunk: np.ndarray) -> None:
                    chunks.append(np.asarray(chunk, dtype=np.float32).copy())

                cap = MicStreamCapture(
                    device_index=device, sample_rate=sr, on_audio=_on_chunk
                )
                cap.start()
                try:
                    for left in range(10, 0, -1):
                        self.enroll_progress.emit(f"录音中 {left}s… 请持续说话（{mic_name}）")
                        _time.sleep(1.0)
                finally:
                    cap.stop()

                if not chunks:
                    raise RuntimeError(f"没有采到音频，请检查麦克风：{mic_name}")
                samples = np.concatenate(chunks)
                if len(samples) < sr * 3:
                    raise RuntimeError("录音太短或为空，请重试并大声说话")
                peak = float(np.max(np.abs(samples)))
                win_rms = speech_window_rms(samples, sr)
                full_rms = float(np.sqrt(np.mean(np.square(samples))))
                logger.info(
                    "音色录音完成: device=%s peak=%.4f win_rms=%.4f full_rms=%.4f sec=%.2f",
                    mic_name,
                    peak,
                    win_rms,
                    full_rms,
                    len(samples) / sr,
                )
                if win_rms < 0.008 and peak < 0.02:
                    raise RuntimeError(
                        f"几乎没有人声（设备: {mic_name}，peak={peak:.4f}）。\n"
                        "请确认选的是插孔麦克风、系统没静音，并对着麦大声说满 10 秒。"
                    )
                samples = normalize_speech(samples)
                wav = float32_to_wav_bytes(samples, sr)
                model = (
                    self._config.get("bailian", {}).get(
                        "model", "qwen3.5-livetranslate-flash-realtime"
                    )
                    or "qwen3.5-livetranslate-flash-realtime"
                )
                self.enroll_progress.emit("正在上传并创建音色…")
                voice_id = create_livetranslate_voice(
                    api_key,
                    wav,
                    target_model=model,
                    preferred_name="meeting",
                    language="zh",
                )
            except Exception as exc:
                logger.exception("录制/创建音色失败")
                err = str(exc)
            self.enroll_finished.emit(voice_id, err)

        threading.Thread(target=_run, daemon=True).start()

    def _on_enroll_progress(self, msg: str) -> None:
        self._set_preload_status(msg, "#2563eb")

    def _on_enroll_done(self, voice_id: str, err: str) -> None:
        self._enroll_voice_btn.setEnabled(True)
        if err or not voice_id:
            self._set_preload_status("")
            QMessageBox.warning(self, "创建音色失败", err or "未知错误")
            self._sync_voice_mode_ui()
            return

        bailian = self._config.setdefault("bailian", {})
        bailian["zh_en_custom_voice_id"] = voice_id
        bailian["zh_en_voice_mode"] = "custom"
        for i in range(self._voice_mode_combo.count()):
            if self._voice_mode_combo.itemData(i) == "custom":
                self._voice_mode_combo.setCurrentIndex(i)
                break
        self._voice_value_edit.setText(voice_id)
        try:
            if self._config_path is not None:
                save_config(self._config, self._config_path)
        except Exception as exc:
            logger.exception("保存音色 ID 失败")
            QMessageBox.warning(self, "保存失败", str(exc))
        self._set_preload_status(f"音色已创建: {voice_id}", "#16a34a")
        self._sync_voice_mode_ui()
        QMessageBox.information(
            self,
            "音色已就绪",
            f"已创建固定复刻音色：\n{voice_id}\n\n"
            "请再点左侧「确定」，然后开始中→英。此后英文播报将使用该音色。",
        )

    def _confirm_engine(self) -> None:
        if self._pipelines_busy():
            QMessageBox.warning(self, "无法切换", "请先停止监听后再确定。")
            return
        if self._loading_local:
            return

        new_backend = self._selected_backend_from_ui()
        if new_backend == BACKEND_BAILIAN and not self._bailian_api_key():
            QMessageBox.warning(self, "缺少 API Key", "请先填写阿里百炼 API Key。")
            return

        self._backend = new_backend
        self._persist_engine_to_config(save_file=True)
        self._rebuild_pipelines()
        self._clear_en_entries()
        self._zh_history_blocks.clear()
        self._zh_partial_text = ""
        self._zh_live.clear()
        self._zh_audio_warning = ""
        self._update_stream_placeholders()
        self._ready = False
        self._engine_confirmed = False
        self._set_controls_enabled(False)

        if self._backend == BACKEND_BAILIAN:
            self.preload_finished.emit(True, "百炼双流式就绪（无需加载本地模型）。")
            return

        self._ensure_local_resources()
        if self._local_preloaded and self._resources is not None and self._resources.is_ready:
            self.preload_finished.emit(True, "本地模型已就绪。")
            return

        self._start_preload()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._en_toggle_btn.setEnabled(enabled and not self._en_busy)
        if self._ptt_btn is not None:
            self._ptt_btn.setEnabled(
                enabled and self._zh_input_mode == "ptt" and not self._zh_busy
            )
        if self._zh_toggle_btn is not None:
            self._zh_toggle_btn.setEnabled(
                enabled
                and self._zh_input_mode == "continuous"
                and not self._zh_busy
            )
        self._test_vmic_btn.setEnabled(enabled and not self._zh_busy)
        busy = self._pipelines_busy() or self._loading_local
        self._local_radio.setEnabled(not busy)
        self._bailian_radio.setEnabled(not busy)
        self._confirm_engine_btn.setEnabled(not busy)
        self._api_key_edit.setEnabled(not busy)
        self._workspace_edit.setEnabled(not busy)
        self._voice_mode_combo.setEnabled(not busy)
        self._voice_value_edit.setEnabled(not busy)
        self._api_key_edit.setReadOnly(busy)
        self._workspace_edit.setReadOnly(busy)
        self._voice_value_edit.setReadOnly(busy)
        if hasattr(self, "_zh_continuous_radio"):
            self._zh_continuous_radio.setEnabled(not busy)
            self._zh_ptt_radio.setEnabled(not busy)
        if not busy:
            self._sync_voice_mode_ui()
        self._set_quick_controls_enabled(enabled and not self._quick_busy)
        self._sync_zh_input_mode_ui()

    def _sync_zh_input_mode_ui(self) -> None:
        if not hasattr(self, "_zh_continuous_radio"):
            return
        continuous = self._zh_input_mode == "continuous"
        if self._zh_toggle_btn is not None:
            self._zh_toggle_btn.setVisible(continuous)
        if self._ptt_btn is not None:
            self._ptt_btn.setVisible(not continuous)
        if continuous:
            self._zh_mode_hint.setText(
                "持续拾取麦克风；建议戴耳机。"
                "「跟随我的声音」需连续说几句后才会像你（复刻完成前可能是默认女声）。"
            )
        else:
            self._zh_mode_hint.setText(
                "按住 Space / 按钮说话。"
                "「跟随我的声音」在按住说话下会按每句实时复刻；请尽量按住说完一整句。"
            )

    def _on_zh_input_mode_changed(self, checked: bool = True) -> None:
        if not checked:
            return
        if not hasattr(self, "_zh_continuous_radio"):
            return
        new_mode = "ptt" if self._zh_ptt_radio.isChecked() else "continuous"
        if new_mode == self._zh_input_mode:
            return

        if self._pipelines_busy():
            QMessageBox.warning(
                self, "无法切换", "请先停止中→英监听（或松开按住说话）后再切换。"
            )
            self._zh_continuous_radio.blockSignals(True)
            self._zh_ptt_radio.blockSignals(True)
            if self._zh_input_mode == "ptt":
                self._zh_ptt_radio.setChecked(True)
            else:
                self._zh_continuous_radio.setChecked(True)
            self._zh_continuous_radio.blockSignals(False)
            self._zh_ptt_radio.blockSignals(False)
            return

        self._zh_input_mode = new_mode
        self._config.setdefault("si", {})["zh_input_mode"] = new_mode
        if self._zh_pipeline is not None and hasattr(self._zh_pipeline, "_input_mode"):
            self._zh_pipeline._input_mode = new_mode
        if self._config_path is not None:
            try:
                save_config(self._config, self._config_path)
            except Exception:
                logger.exception("保存中→英输入方式失败")
        self._sync_zh_input_mode_ui()
        self._set_controls_enabled(self._ready)

    def _set_quick_controls_enabled(self, enabled: bool) -> None:
        if not hasattr(self, "_quick_input"):
            return
        self._quick_input.setEnabled(enabled)
        self._quick_translate_btn.setEnabled(enabled)
        self._quick_mic_btn.setEnabled(enabled)
        self._quick_copy_btn.setEnabled(enabled and bool(self._quick_last_translation))

    def _on_quick_translate_clicked(self) -> None:
        text = self._quick_input.text().strip()
        if not text:
            return
        if not self._ready or not self._engine_confirmed:
            self._quick_status_label.setText("请先确认翻译引擎")
            return
        self._start_quick_translate(text)

    def _start_quick_translate(self, text: str) -> None:
        if self._quick_busy:
            return
        self._quick_busy = True
        self._quick_input.clear()
        self._set_quick_controls_enabled(False)
        self._quick_status_label.setText("翻译中…")

        backend = self._backend
        api_key = self._bailian_api_key()
        llm_cfg = self._config.get("llm", {}) or {}
        model = (llm_cfg.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        resources = self._resources

        def _run() -> None:
            try:
                from translate.lang_detect import detect_zh_or_en

                lang = detect_zh_or_en(text)
                if lang == "zh":
                    from_code, to_code = "zh", "en"
                    direction = "中 → 英"
                    user_hint = f"请将下列中文翻译成英文：\n{text}"
                else:
                    from_code, to_code = "en", "zh"
                    direction = "英 → 中"
                    user_hint = f"请将下列英文翻译成中文：\n{text}"

                if backend == BACKEND_LOCAL:
                    if resources is None or not resources.is_ready:
                        raise RuntimeError("本地模型未就绪，请先点击「确定」加载。")
                    translated = resources.translator.translate(
                        text, from_code, to_code
                    )
                else:
                    translated = chat_completion(
                        api_key,
                        user_hint,
                        model=model,
                        system_prompt=QUICK_TRANSLATE_SYSTEM_PROMPT,
                    )
                if not (translated or "").strip():
                    raise RuntimeError("译文为空")
                self.quick_translate_finished.emit(text, translated.strip(), direction)
            except Exception as exc:
                logger.exception("快速翻译失败")
                self.quick_translate_error.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _history_chinese_text(self, item: HistoryItem) -> str:
        """取该条记录中的中文内容（中→英用原文，英→中用译文）。"""
        direction = (item.direction or "").strip()
        if direction.startswith("中"):
            return (item.source or "").strip()
        if direction.startswith("英"):
            return (item.target or "").strip()
        from translate.lang_detect import detect_zh_or_en

        if detect_zh_or_en(item.source) == "zh":
            return (item.source or "").strip()
        if detect_zh_or_en(item.target) == "zh":
            return (item.target or "").strip()
        return (item.source or item.target or "").strip()

    def _history_item_display(self, item: HistoryItem) -> str:
        chinese = self._history_chinese_text(item)
        if len(chinese) > 15:
            chinese = chinese[:15] + "..."
        return chinese or "（空）"

    def _prepend_history_item(self, item: HistoryItem) -> None:
        if not hasattr(self, "_history_list"):
            return
        row = QListWidgetItem(self._history_item_display(item))
        row.setData(Qt.ItemDataRole.UserRole, item)
        self._history_list.insertItem(0, row)

    def _load_translate_history(self) -> None:
        if not hasattr(self, "_history_list"):
            return
        self._history_list.clear()
        try:
            items = self._history_store.list_recent(100)
        except Exception:
            logger.exception("加载翻译历史失败")
            return
        for item in items:
            row = QListWidgetItem(self._history_item_display(item))
            row.setData(Qt.ItemDataRole.UserRole, item)
            self._history_list.addItem(row)

    def _on_history_item_clicked(self, list_item: QListWidgetItem) -> None:
        data = list_item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, HistoryItem):
            return
        self._quick_last_translation = data.target
        self._quick_result.setHtml(
            f'<span style="color:{COLOR_META};font-size:12px;">[{escape(data.direction)}]</span><br>'
            f'<span style="color:{COLOR_SOURCE};">原文：{escape(data.source)}</span><br>'
            f'<span style="color:{COLOR_TARGET};font-weight:600;">译文：{escape(data.target)}</span>'
        )
        self._quick_status_label.setText("已从历史回填")
        self._quick_copy_btn.setEnabled(True)

    def _on_clear_history(self) -> None:
        reply = QMessageBox.question(
            self,
            "清空历史",
            "确定清空全部翻译历史？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._history_store.clear()
        except Exception as exc:
            logger.exception("清空历史失败")
            QMessageBox.warning(self, "清空失败", str(exc))
            return
        self._history_list.clear()

    def _on_quick_translate_finished(
        self, source: str, target: str, direction: str
    ) -> None:
        self._quick_busy = False
        self._quick_last_translation = target
        self._quick_result.setHtml(
            f'<span style="color:{COLOR_META};font-size:12px;">[{escape(direction)}]</span><br>'
            f'<span style="color:{COLOR_SOURCE};">原文：{escape(source)}</span><br>'
            f'<span style="color:{COLOR_TARGET};font-weight:600;">译文：{escape(target)}</span>'
        )
        self._quick_status_label.setText("完成")
        self._set_quick_controls_enabled(self._ready)
        try:
            item = self._history_store.add(
                direction=direction,
                source=source,
                target=target,
                backend=self._backend,
            )
            self._prepend_history_item(item)
        except Exception:
            logger.exception("保存翻译历史失败")

    def _on_quick_translate_error(self, msg: str) -> None:
        self._quick_busy = False
        self._quick_status_label.setText("失败")
        self._quick_result.setHtml(
            f'<span style="color:#b91c1c;">翻译失败：{escape(msg)}</span>'
        )
        self._set_quick_controls_enabled(self._ready)

    def _on_quick_copy(self) -> None:
        if not self._quick_last_translation:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._quick_last_translation)
            self._quick_status_label.setText("已复制译文")

    def _ensure_quick_mic(self):
        if self._quick_mic_recorder is not None:
            return self._quick_mic_recorder
        from audio.devices import resolve_input_device
        from audio.mic import MicRecorder

        audio_cfg = self._config.get("audio", {})
        device = resolve_input_device(audio_cfg.get("microphone_device", "") or "")
        sr = int(audio_cfg.get("sample_rate", 16000) or 16000)
        self._quick_mic_recorder = MicRecorder(device_index=device, sample_rate=sr)
        return self._quick_mic_recorder

    def _on_quick_mic_press(self) -> None:
        if self._quick_busy or not self._ready:
            return
        local_ready = self._local_preloaded or (
            self._resources is not None and self._resources.is_ready
        )
        if not local_ready:
            self._quick_mic_btn.setChecked(False)
            self._quick_status_label.setText("语音需本地模型，请改用打字")
            QMessageBox.information(
                self,
                "语音输入不可用",
                "语音识别需要本地 Whisper 模型。\n"
                "请切换到「本地离线」并点击「确定」加载，或直接打字翻译。",
            )
            return
        try:
            mic = self._ensure_quick_mic()
            mic.start()
            self._quick_mic_btn.setChecked(True)
            self._quick_status_label.setText("录音中…")
        except Exception as exc:
            logger.exception("快速翻译麦克风启动失败")
            self._quick_mic_btn.setChecked(False)
            self._quick_status_label.setText("麦克风失败")
            QMessageBox.warning(self, "麦克风错误", str(exc))

    def _on_quick_mic_release(self) -> None:
        self._quick_mic_btn.setChecked(False)
        recorder = self._quick_mic_recorder
        if recorder is None or not recorder.is_recording:
            return
        try:
            audio = recorder.stop()
        except Exception as exc:
            logger.exception("停止录音失败")
            self.quick_asr_error.emit(str(exc))
            return

        if audio is None or len(audio) == 0:
            self._quick_status_label.setText("未录到声音")
            return

        if self._quick_busy:
            return
        self._quick_busy = True
        self._set_quick_controls_enabled(False)
        self._quick_status_label.setText("识别中…")

        resources = self._resources
        sample_rate = int(self._config.get("audio", {}).get("sample_rate", 16000) or 16000)

        def _run() -> None:
            try:
                if resources is None or not resources.is_ready:
                    raise RuntimeError("本地模型未就绪，无法语音识别。")
                # 优先中文 ASR；语种由后续 detect 决定翻译方向
                asr = resources.zh_asr or resources.en_asr
                if asr is None:
                    raise RuntimeError("ASR 未加载")
                text = asr.transcribe(audio, language=None, sample_rate=sample_rate)
                if not (text or "").strip():
                    raise RuntimeError("未识别到有效语音")
                self.quick_asr_finished.emit(text.strip())
            except Exception as exc:
                logger.exception("快速翻译 ASR 失败")
                self.quick_asr_error.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_quick_asr_finished(self, text: str) -> None:
        self._quick_busy = False
        self._quick_input.setText(text)
        self._quick_status_label.setText("识别完成，正在翻译…")
        self._start_quick_translate(text)

    def _on_quick_asr_error(self, msg: str) -> None:
        self._quick_busy = False
        self._quick_status_label.setText("识别失败")
        self._quick_result.setHtml(
            f'<span style="color:#b91c1c;">语音识别失败：{escape(msg)}</span>'
        )
        self._set_quick_controls_enabled(self._ready)

    def _start_preload(self) -> None:
        if self._resources is None:
            from pipeline.resources import AppResources

            self._resources = AppResources(self._config)

        self._loading_local = True
        self._set_controls_enabled(False)
        self._set_preload_status("正在加载本地模型…", "#b45309")
        self._en_status_label.setText("加载中…")
        self._zh_status_label.setText("加载中…")

        def _run() -> None:
            try:
                self._ensure_local_resources()
                self._resources.preload(
                    on_progress=lambda msg: self.preload_progress.emit(msg)
                )
                self.preload_finished.emit(True, "本地模型已加载，可以开始使用。")
            except Exception as exc:
                self.preload_finished.emit(False, str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_preload_progress(self, msg: str) -> None:
        self._set_preload_status(msg, "#b45309")

    def _on_preload_finished(self, success: bool, msg: str) -> None:
        self._loading_local = False
        if success:
            self._ready = True
            self._engine_confirmed = True
            if self._backend == BACKEND_LOCAL and self._resources is not None:
                self._local_preloaded = self._resources.is_ready
            self._set_preload_status("✓ " + msg, "#15803d")
            self._en_status_label.setText("未启动")
            self._zh_status_label.setText("就绪")
            self._update_stream_placeholders()
            self._set_controls_enabled(True)
        else:
            self._ready = False
            self._engine_confirmed = False
            self._set_preload_status("✗ 加载失败: " + msg, "#b91c1c")
            self._en_status_label.setText("加载失败")
            self._zh_status_label.setText("加载失败")
            self._set_controls_enabled(False)
            self._sync_backend_ui()

    def _sync_bailian_credentials(self) -> None:
        self._persist_engine_to_config(save_file=False)
        key = self._bailian_api_key()
        if hasattr(self._en_pipeline, "update_credentials"):
            self._en_pipeline.update_credentials(key, self._config)
        if hasattr(self._zh_pipeline, "update_credentials"):
            self._zh_pipeline.update_credentials(key, self._config)

    def _toggle_en_pipeline(self) -> None:
        if not self._ready or self._en_busy or self._en_pipeline is None:
            return

        starting = not self._en_running
        self._en_busy = True
        self._en_toggle_btn.setEnabled(False)
        self._en_toggle_btn.setText("正在启动…" if starting else "正在停止…")
        self._en_status_label.setText(
            "正在启动英→中…" if starting else "正在停止英→中，请稍候…"
        )

        if starting and self._backend == BACKEND_BAILIAN:
            try:
                self._sync_bailian_credentials()
            except Exception as exc:
                self._en_busy = False
                self._en_toggle_btn.setText("开始英→中")
                self._en_status_label.setText(f"启动失败: {exc}")
                self._set_controls_enabled(self._ready)
                return

        pipeline = self._en_pipeline

        def _run() -> None:
            try:
                if starting:
                    pipeline.start()
                else:
                    pipeline.stop()
                self.en_op_finished.emit(starting, True, "")
            except Exception as exc:
                logger.exception("英→中%s失败", "启动" if starting else "停止")
                self.en_op_finished.emit(starting, False, str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_en_op_finished(self, starting: bool, success: bool, error: str) -> None:
        self._en_busy = False
        if starting:
            if success:
                self._en_running = True
                self._en_toggle_btn.setText("停止英→中")
                if not (self._en_status_label.text() or "").strip():
                    self._en_status_label.setText("英→中监听中")
            else:
                self._en_running = False
                self._en_toggle_btn.setText("开始英→中")
                self._en_status_label.setText(f"启动失败: {error}")
        else:
            self._en_running = False
            self._en_toggle_btn.setText("开始英→中")
            if success:
                self._en_status_label.setText("未启动")
            else:
                self._en_status_label.setText(f"停止异常: {error}")
        self._set_controls_enabled(self._ready)

    def _toggle_zh_continuous(self) -> None:
        if self._zh_input_mode != "continuous":
            return
        if not self._ready or self._zh_busy or self._zh_pipeline is None:
            return

        starting = not self._zh_continuous_running
        self._zh_busy = True
        self._zh_toggle_btn.setEnabled(False)
        self._zh_toggle_btn.setText("正在启动…" if starting else "正在停止…")
        self._zh_status_label.setText(
            "正在启动中→英…" if starting else "正在停止中→英，请稍候…"
        )

        if starting and self._backend == BACKEND_BAILIAN:
            try:
                self._sync_bailian_credentials()
            except Exception as exc:
                self._zh_busy = False
                self._zh_toggle_btn.setText("开始中→英")
                self._zh_status_label.setText(f"启动失败: {exc}")
                self._set_controls_enabled(self._ready)
                return

        pipeline = self._zh_pipeline

        def _run() -> None:
            try:
                if starting:
                    pipeline.start_continuous()
                else:
                    pipeline.stop_continuous()
                self.zh_op_finished.emit(starting, True, "")
            except Exception as exc:
                logger.exception("中→英%s失败", "启动" if starting else "停止")
                self.zh_op_finished.emit(starting, False, str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_zh_op_finished(self, starting: bool, success: bool, error: str) -> None:
        self._zh_busy = False
        if starting:
            if success:
                self._zh_continuous_running = True
                self._zh_toggle_btn.setText("停止中→英")
            else:
                self._zh_continuous_running = False
                self._zh_toggle_btn.setText("开始中→英")
                self._zh_status_label.setText(f"启动失败: {error}")
        else:
            self._zh_continuous_running = False
            self._zh_toggle_btn.setText("开始中→英")
            if success:
                self._zh_status_label.setText("就绪")
            else:
                self._zh_status_label.setText(f"停止异常: {error}")
        self._set_controls_enabled(self._ready)
    def _on_ptt_press(self) -> None:
        if not self._ready or self._zh_input_mode != "ptt":
            return
        self._ptt_active = True
        self._set_controls_enabled(self._ready)
        if self._ptt_btn:
            self._ptt_btn.setText("录音中…")
        try:
            if self._backend == BACKEND_BAILIAN:
                self._sync_bailian_credentials()
            self._zh_pipeline.ptt_press()
        except Exception as exc:
            self._ptt_active = False
            if self._ptt_btn:
                self._ptt_btn.setText("按住说话 (Space)")
                self._ptt_btn.setChecked(False)
            self._zh_status_label.setText(f"错误: {exc}")
            self._set_controls_enabled(self._ready)

    def _on_test_virtual_mic(self) -> None:
        if not self._ready:
            return
        try:
            self._zh_pipeline.test_virtual_mic()
        except Exception as exc:
            self._zh_status_label.setText(f"测试失败: {exc}")

    def _on_ptt_release(self) -> None:
        if self._zh_input_mode != "ptt":
            return
        if not self._ptt_active:
            return
        self._ptt_active = False
        if self._ptt_btn:
            self._ptt_btn.setText("按住说话 (Space)")
            self._ptt_btn.setChecked(False)
        if self._zh_pipeline is not None:
            self._zh_pipeline.ptt_release()
        self._set_controls_enabled(self._ready)

    def _send_to_llm(self, chinese: str, button: QPushButton | None = None) -> None:
        text = (chinese or "").strip()
        if not text or text == "…":
            QMessageBox.information(self, "无法发送", "该条没有可用的中文译文。")
            return
        api_key = self._bailian_api_key()
        if not api_key:
            QMessageBox.warning(
                self, "缺少 API Key", "发送到大模型需要填写阿里百炼 API Key。"
            )
            return
        if self._llm_busy:
            QMessageBox.information(self, "请稍候", "上一次请求仍在进行中。")
            return

        model = self._llm_model_edit.text().strip() or DEFAULT_MODEL
        system_prompt = (
            self._llm_prompt_edit.toPlainText().strip() or DEFAULT_SYSTEM_PROMPT
        )
        self._persist_engine_to_config(save_file=False)

        self._llm_busy = True
        self._llm_active_button = button
        self._llm_status_label.setText("流式生成中…")
        if button is not None:
            button.setEnabled(False)
            button.setText("…")

        def _run() -> None:
            try:
                self.llm_stream_started.emit(text)
                got_any = False
                for chunk in chat_completion_stream(
                    api_key,
                    text,
                    model=model,
                    system_prompt=system_prompt,
                ):
                    if chunk:
                        got_any = True
                        self.llm_stream_delta.emit(chunk)
                if not got_any:
                    raise RuntimeError("大模型返回空内容")
                self.llm_stream_finished.emit()
            except Exception as exc:
                logger.exception("大模型请求失败")
                self.llm_error.emit(str(exc))

        threading.Thread(target=_run, daemon=True).start()

    def _on_llm_stream_started(self, chinese: str) -> None:
        self._llm_streaming = True
        self._llm_stream_ts = datetime.now().strftime("%H:%M:%S")
        summary = chinese if len(chinese) <= 60 else chinese[:57] + "…"
        self._llm_stream_question = summary
        self._llm_stream_answer = ""
        self._llm_reply_blocks.append(self._format_llm_block(streaming=True))
        max_lines = self._config.get("ui", {}).get("max_subtitle_lines", 50)
        while len(self._llm_reply_blocks) > max_lines:
            self._llm_reply_blocks.pop(0)
        self._render_llm_replies()
        self._llm_status_label.setText("流式生成中…")

    def _on_llm_stream_delta(self, chunk: str) -> None:
        if not self._llm_streaming:
            return
        self._llm_stream_answer += chunk
        if self._llm_reply_blocks:
            self._llm_reply_blocks[-1] = self._format_llm_block(streaming=True)
        self._render_llm_replies()

    def _on_llm_stream_finished(self) -> None:
        self._llm_streaming = False
        self._llm_busy = False
        if self._llm_reply_blocks:
            self._llm_reply_blocks[-1] = self._format_llm_block(streaming=False)
        self._render_llm_replies()
        self._restore_llm_button()
        self._llm_status_label.setText("已收到回复")

    def _format_llm_block(self, *, streaming: bool) -> str:
        answer = self._llm_stream_answer or ("…" if streaming else "")
        suffix = " ▌" if streaming else ""
        return (
            f"[{self._llm_stream_ts}]\n"
            f"问: {self._llm_stream_question}\n"
            f"答: {answer}{suffix}"
        )

    def _on_llm_error(self, message: str) -> None:
        was_streaming = self._llm_streaming
        self._llm_streaming = False
        self._llm_busy = False
        self._restore_llm_button()
        if was_streaming and self._llm_reply_blocks and not self._llm_stream_answer:
            self._llm_reply_blocks.pop()
            self._render_llm_replies()
        elif was_streaming and self._llm_reply_blocks:
            self._llm_reply_blocks[-1] = self._format_llm_block(streaming=False)
            self._render_llm_replies()
        self._llm_status_label.setText(f"失败: {message}")
        QMessageBox.warning(self, "大模型请求失败", message)

    def _restore_llm_button(self) -> None:
        button = getattr(self, "_llm_active_button", None)
        if button is not None:
            try:
                button.setEnabled(True)
                button.setText("发送")
            except RuntimeError:
                pass
        self._llm_active_button = None

    def _append_subtitle(self, entry) -> None:
        partial = getattr(entry, "partial", False)
        english = entry.english or "…"
        chinese = entry.chinese or "…"

        if partial and self._backend == BACKEND_BAILIAN:
            self._en_partial_text = self._format_bilingual_html(
                source_label="EN",
                source=english,
                target_label="ZH",
                target=chinese,
            )
            self._update_en_partial()
            return

        ts = datetime.now().strftime("%H:%M:%S")
        self._en_partial_text = ""
        self._update_en_partial()
        self._add_en_entry_row(
            EnSubtitleEntry(ts=ts, english=english, chinese=chinese)
        )

    def _append_zh_en(self, result) -> None:
        partial = getattr(result, "partial", False)
        chinese = result.chinese or "…"
        english = result.english or "…"
        warning = getattr(result, "warning", "")

        if partial and self._backend == BACKEND_BAILIAN:
            self._zh_audio_warning = ""
            self._set_zh_live_warning(False)
            self._zh_partial_text = self._format_bilingual_html(
                source_label="ZH",
                source=chinese,
                target_label="EN",
                target=english,
            )
            self._render_stream(
                self._zh_live,
                self._zh_history_blocks,
                self._zh_partial_text,
                partial_is_html=True,
            )
            return

        ts = datetime.now().strftime("%H:%M:%S")
        if warning:
            self._zh_audio_warning = f"请重说\n{warning}"
            self._zh_partial_text = ""
            self._set_zh_live_warning(True)
            current_text = self._zh_audio_warning
            partial_is_html = False
        elif self._backend == BACKEND_BAILIAN:
            self._zh_audio_warning = ""
            self._zh_partial_text = ""
            self._set_zh_live_warning(False)
            current_text = ""
            partial_is_html = False
        else:
            self._zh_audio_warning = ""
            self._zh_partial_text = ""
            self._set_zh_live_warning(False)
            current_text = ""
            partial_is_html = False
        block = self._format_bilingual_html(
            source_label="ZH",
            source=chinese,
            target_label="EN",
            target=english,
            ts=ts,
            warning=warning or "",
        )
        self._zh_history_blocks.append(block)
        self._trim_stream_blocks(self._zh_history_blocks)
        self._render_stream(
            self._zh_live,
            self._zh_history_blocks,
            current_text,
            partial_is_html=partial_is_html,
        )

    def closeEvent(self, event) -> None:
        if self._en_running and self._en_pipeline:
            self._en_pipeline.stop()
        if self._zh_continuous_running and self._zh_pipeline:
            self._zh_pipeline.stop_continuous()
        if self._backend == BACKEND_BAILIAN and self._zh_pipeline is not None:
            session = getattr(self._zh_pipeline, "_session", None)
            if session is not None:
                try:
                    session.stop()
                except Exception:
                    pass
        event.accept()


def run_app(config: dict, config_path: Path | None = None) -> None:
    logger.info("正在启动界面…")
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(APP_STYLESHEET)
    window = MainWindow(config, config_path=config_path)
    window.show()
    logger.info("界面已显示")
    app.exec()
