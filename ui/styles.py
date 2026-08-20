"""应用全局样式（圆角、配色）。"""

APP_STYLESHEET = """
QMainWindow, QWidget#root {
    background-color: #eef1f6;
    color: #1e293b;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}

#settingsPanel {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 16px;
}

#settingsScroll {
    background: transparent;
    border: none;
}

#settingsInner {
    background: transparent;
}

#contentPanel {
    background-color: transparent;
}

#streamCard {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 16px;
}

#streamTitle {
    font-size: 15px;
    font-weight: 600;
    color: #334155;
    padding: 4px 0;
}

#liveStream {
    background-color: #f8fafc;
    border: 1px solid #dbe4f0;
    border-radius: 12px;
    padding: 10px;
    font-size: 14px;
}

#liveStreamEn {
    background-color: #eff6ff;
    border: 1px solid #bfdbfe;
    border-radius: 12px;
    padding: 10px;
    font-size: 14px;
}

#enScrollArea {
    background-color: #eff6ff;
    border: 1px solid #bfdbfe;
    border-radius: 12px;
}

#enScrollArea > QWidget > QWidget {
    background-color: transparent;
}

#enEntryRow {
    background-color: #ffffff;
    border: 1px solid #dbeafe;
    border-radius: 10px;
}

#enEntryText {
    font-size: 13px;
}

#enPartialLabel {
    font-size: 14px;
    padding: 6px 4px;
}

QPushButton#sendLlmBtn {
    background-color: #0ea5e9;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 6px 12px;
    font-weight: 600;
    min-width: 56px;
    min-height: 28px;
}

QPushButton#sendLlmBtn:hover {
    background-color: #0284c7;
}

QPushButton#sendLlmBtn:disabled {
    background-color: #cbd5e1;
    color: #94a3b8;
}

#llmReplyStream {
    background-color: #faf5ff;
    border: 1px solid #e9d5ff;
    border-radius: 12px;
    padding: 10px;
    font-size: 13px;
}

#liveStreamZh {
    background-color: #f0fdf4;
    border: 1px solid #bbf7d0;
    border-radius: 12px;
    padding: 10px;
    font-size: 14px;
}

#historyStream {
    background-color: #fafafa;
    border: 1px solid #e8edf3;
    border-radius: 10px;
    padding: 8px;
    margin-bottom: 2px;
    font-size: 12px;
    color: #475569;
}

QPushButton {
    background-color: #3b82f6;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 4px 12px;
    font-weight: 600;
    min-height: 26px;
    max-height: 32px;
}

QPushButton:hover {
    background-color: #2563eb;
}

QPushButton:disabled {
    background-color: #cbd5e1;
    color: #94a3b8;
}

QPushButton#secondaryBtn {
    background-color: #e2e8f0;
    color: #334155;
}

QPushButton#secondaryBtn:hover {
    background-color: #cbd5e1;
}

QLineEdit {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 2px 8px;
    background: #f8fafc;
    min-height: 22px;
    max-height: 28px;
}

QLineEdit:focus {
    border: 1px solid #3b82f6;
    background: #ffffff;
}

QComboBox {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 2px 6px;
    background: #f8fafc;
    min-height: 22px;
    max-height: 28px;
}

QTextEdit#llmPromptEdit {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px 6px;
    background: #f8fafc;
    font-size: 12px;
}

QGroupBox {
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    margin-top: 10px;
    padding-top: 12px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #475569;
}

QCheckBox {
    spacing: 6px;
}

#zhModeSeparator {
    min-height: 1px;
    max-height: 1px;
    background: transparent;
    border: none;
}

#statusLabel {
    color: #64748b;
    font-size: 12px;
}

#preloadLabel {
    font-weight: 600;
}

#quickTranslateCard {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 16px;
}

#quickTranslateTitle {
    font-size: 15px;
    font-weight: 600;
    color: #334155;
    padding: 2px 0;
}

#quickTranslateResult {
    background-color: #f8fafc;
    border: 1px solid #dbe4f0;
    border-radius: 10px;
    padding: 8px 10px;
    font-size: 13px;
    color: #334155;
}

QPushButton#quickMicBtn {
    background-color: #0ea5e9;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 4px 12px;
    font-weight: 600;
    min-height: 26px;
    max-height: 32px;
}

QPushButton#quickMicBtn:hover {
    background-color: #0284c7;
}

QPushButton#quickMicBtn:pressed,
QPushButton#quickMicBtn:checked {
    background-color: #0369a1;
}

QPushButton#quickMicBtn:disabled {
    background-color: #cbd5e1;
    color: #94a3b8;
}

#historyList {
    background-color: #f8fafc;
    border: 1px solid #dbe4f0;
    border-radius: 10px;
    padding: 4px;
    outline: none;
    color: #1e293b;
}

#historyList::item {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 6px 8px;
    margin: 2px 1px;
    color: #1e293b;
}

#historyList::item:selected {
    background-color: #eff6ff;
    border: 1px solid #bfdbfe;
    color: #1e293b;
}

#historyList::item:selected:active {
    background-color: #dbeafe;
    border: 1px solid #93c5fd;
    color: #1e293b;
}

#historyList::item:hover {
    background-color: #f1f5f9;
    color: #1e293b;
}
"""
