"""Anomaly-tab input loading, build process and cooperative Stop actions."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

from PyQt6 import QtCore, QtGui, QtWidgets

from gui.logging_utils import GuiLogger


class AnomalyTabController(QtCore.QObject):
    activeChanged = QtCore.pyqtSignal(bool)

    def __init__(self, window, store, *, project_root=None):
        super().__init__(window)
        self.window = window
        self.store = store
        self.project_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
        self.process = None
        self._stdout = b""
        self._stderr = b""
        self._request = b""
        self._available = True
        self.logger = GuiLogger(window.theoryBuildLog.appendPlainText)
        window.loadTheoriesButton.clicked.connect(self.load_theories)
        window.buildTheoriesButton.clicked.connect(self.build_theories)

    def set_available(self, available):
        """Let the main window prevent a build while another tab is busy."""
        self._available = available
        if self.process is None:
            self.window.buildTheoriesButton.setEnabled(available)

    def start_log(self):
        return self.logger.start_file(
            self.project_root / "logs", prefix="log_anomalies", label="anomaly log",
        )

    def _set_build_active(self, active):
        self.window.theoriesInput.setReadOnly(active)
        self.window.loadTheoriesButton.setEnabled(not active)
        self.window.buildCharacterCacheCheckBox.setEnabled(not active)
        self.window.buildTheoriesButton.setText("Stop" if active else "Build")
        self.window.buildTheoriesButton.setEnabled(True)
        self.activeChanged.emit(active)

    def build_theories(self):
        if not self._available:
            return
        if self.process is not None:
            self.stop()
            return
        self.start_log()
        text = self.window.theoriesInput.toPlainText()
        if not text.strip():
            self.logger.log("Enter at least one gauge group in the left area.", level="ERROR")
            self.logger.close_file()
            return
        try:
            settings = self.store.load()
            if not settings["mysql/database"].strip():
                raise ValueError("Set the MySQL database name in Settings → Preferences.")
            sibling_sage = Path(sys.executable).with_name("sage")
            sage = str(sibling_sage) if sibling_sage.is_file() else shutil.which("sage")
            if sage is None:
                raise ValueError("Sage was not found. Launch the GUI from its Sage environment "
                                 "or put the sage executable on PATH.")
        except (OSError, ValueError) as exc:
            self.logger.log(f"Cannot start build: {exc}", level="ERROR")
            self.logger.close_file()
            return
        self.logger.set_secrets(settings["mysql/password"])
        self._request = (json.dumps({
            "text": text, "settings": settings,
            "build_cache": self.window.buildCharacterCacheCheckBox.isChecked(),
        }) + "\n").encode("utf-8")
        self._stdout = b""
        self._stderr = b""
        process = QtCore.QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(self.project_root))
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.SeparateChannels)
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONDONTWRITEBYTECODE", "1")
        process.setProcessEnvironment(environment)
        process.started.connect(self._send_build_request)
        process.readyReadStandardOutput.connect(self._read_build_output)
        process.readyReadStandardError.connect(lambda: self._read_build_output(stderr=True))
        process.finished.connect(self._build_finished)
        process.errorOccurred.connect(self._build_error)
        self._set_build_active(True)
        self.logger.log("Starting theory build with the saved settings…")
        # A separate main thread is needed for Sage and its spawned cache workers.
        # Credentials travel through stdin, never command arguments or a file.
        process.start(sage, ["-python", "-B", "-u", "-m", "gui.theory_builder"])

    def _send_build_request(self):
        if self.process is not None:
            self.process.write(self._request)
            self._request = b""

    def _read_build_output(self, final=False, *, stderr=False):
        if self.process is None:
            return
        buffer_name = "_stderr" if stderr else "_stdout"
        read = (self.process.readAllStandardError if stderr
                else self.process.readAllStandardOutput)
        lines = (getattr(self, buffer_name) + bytes(read())).split(b"\n")
        remaining = lines.pop()
        if final and remaining:
            lines.append(remaining)
            remaining = b""
        setattr(self, buffer_name, remaining)
        for line in lines:
            text = line.decode("utf-8", errors="replace")
            try:
                record = json.loads(text) if not stderr else None
            except ValueError:
                record = None
            if isinstance(record, dict) and "log" in record:
                self.logger.log(record["log"], level=record.get("level", "INFO"),
                                timestamp=record.get("timestamp"))
            else:
                # Unstructured diagnostics have no declared severity.
                self.logger.log(text, level="WARNING" if stderr else "INFO")

    def stop(self):
        if self.process is None or not self.window.buildTheoriesButton.isEnabled():
            return
        # The worker stops between candidates or after a committed cache order.
        # Keep its pipe open until it exits so it can finish the current operation.
        if self._request:
            self._request += b"stop\n"
        else:
            self.process.write(b"stop\n")
        self.window.buildTheoriesButton.setEnabled(False)
        self.logger.log("Stop requested; waiting for the current operation to finish…")

    def _build_error(self, error):
        if self.process is None:
            return
        self.logger.log(f"Build process: {self.process.errorString()}", level="ERROR")
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._build_finished(-1, QtCore.QProcess.ExitStatus.CrashExit)

    def _build_finished(self, exit_code, exit_status):
        if self.process is None:
            return
        self._read_build_output(final=True)
        self._read_build_output(final=True, stderr=True)
        if exit_status == QtCore.QProcess.ExitStatus.CrashExit or exit_code != 0:
            self.logger.log(f"Build worker exited with code {exit_code}; see error details above.", level="ERROR")
        self.process.deleteLater()
        self.process = None
        self._request = b""
        self.logger.close_file()
        self.logger.set_secrets()
        self._set_build_active(False)

    def load_theories(self) -> None:
        """Append selected text files as one undoable edit, after all reads succeed."""
        filenames, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self.window, "Load theories", str(self.project_root),
            "Text files (*.txt);;All files (*)",
        )
        parts = []
        for filename in filenames:
            try:
                # Accept an optional UTF-8 BOM and normalize platform line endings.
                text = Path(filename).read_text(encoding="utf-8-sig")
            except UnicodeError:
                QtWidgets.QMessageBox.critical(
                    self.window, "Cannot load theories",
                    f"{filename} is not valid UTF-8 text.\n"
                    "Save the file as UTF-8 and try again.",
                )
                return
            except OSError as exc:
                QtWidgets.QMessageBox.critical(
                    self.window, "Cannot load theories", f"Could not read {filename}:\n{exc}",
                )
                return
            if text:
                if parts and not parts[-1].endswith("\n"):
                    parts.append("\n")
                parts.append(text)
        if not parts:
            return
        existing = self.window.theoriesInput.toPlainText()
        separator = "\n" if existing and not existing.endswith("\n") else ""
        cursor = self.window.theoriesInput.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        cursor.insertText(separator + "".join(parts))
        cursor.endEditBlock()
        self.window.theoriesInput.setTextCursor(cursor)
        self.window.theoriesInput.setFocus()
        self.window.theoriesInput.ensureCursorVisible()
