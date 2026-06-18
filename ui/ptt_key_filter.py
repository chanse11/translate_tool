"""全局 Space 键 PTT（避免被 QTextEdit 等控件吃掉）。"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QEvent, Qt


class PttKeyFilter(QObject):
    def __init__(self, window: object) -> None:
        super().__init__()
        self._window = window

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        win = self._window
        if not getattr(win, "_ready", False):
            return False

        if event.type() == QEvent.Type.KeyPress:
            key_event = event
            if (
                key_event.key() == Qt.Key.Key_Space
                and not key_event.isAutoRepeat()
                and not getattr(win, "_ptt_active", False)
            ):
                win._on_ptt_press()
                return True

        if event.type() == QEvent.Type.KeyRelease:
            key_event = event
            if (
                key_event.key() == Qt.Key.Key_Space
                and not key_event.isAutoRepeat()
                and getattr(win, "_ptt_active", False)
            ):
                win._on_ptt_release()
                return True

        return False
