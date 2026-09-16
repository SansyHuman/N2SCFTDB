"""Password confirmation and asynchronous database-content deletion."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

from PyQt6 import QtCore, QtWidgets, uic

from gui.logging_utils import redact_message


class DeleteDatabaseDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        uic.loadUi(str(Path(__file__).resolve().with_name("database_clear_dialog.ui")), self)
        self.settings = {key: value for key, value in settings.items()
                         if key.startswith("mysql/") and key != "mysql/password"}
        self.process = None
        self.deleted_count = None
        self._request = b""
        self._password = ""
        self.warningIcon.setPixmap(self.style().standardIcon(
            QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(40, 40))
        endpoint = (f"Unix socket: {self.settings['mysql/unix_socket']}"
                    if self.settings.get("mysql/unix_socket") else
                    f"Server: {self.settings['mysql/host']}:{self.settings['mysql/port']}")
        self.targetLabel.setText(
            f"Database: {self.settings['mysql/database']}\n{endpoint}\n"
            f"Account: {self.settings['mysql/user']}",
        )
        self.cancelButton.clicked.connect(self.reject)
        self.deleteButton.clicked.connect(self._delete)
        self.passwordEdit.textChanged.connect(
            lambda text: self.deleteButton.setEnabled(bool(text) and self.process is None)
        )
        self.passwordEdit.setFocus()

    def _delete(self):
        if self.process is not None or not self.passwordEdit.text():
            return
        sibling = Path(sys.executable).with_name("sage")
        sage = str(sibling) if sibling.is_file() else shutil.which("sage")
        if sage is None:
            self.statusLabel.setText("Sage was not found. Launch from its Sage environment or put sage on PATH.")
            return
        self._password = self.passwordEdit.text()  # Preserve password whitespace.
        self._request = (json.dumps({
            "settings": self.settings, "password": self._password, "confirm_delete": True,
        }) + "\n").encode("utf-8")
        process = QtCore.QProcess(self)
        self.process = process
        self.passwordEdit.clear()
        self.passwordEdit.setEnabled(False)
        self.deleteButton.setEnabled(False)
        self.cancelButton.setEnabled(False)
        self.statusLabel.setText("Authenticating and deleting… Please wait for the result.")
        process.setWorkingDirectory(str(Path(__file__).resolve().parents[1]))
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.SeparateChannels)
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONDONTWRITEBYTECODE", "1")
        process.setProcessEnvironment(environment)
        process.started.connect(self._send_request)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        # The entered credential is sent over stdin, never argv, logs or files.
        process.start(sage, ["-python", "-B", "-u", "-m", "gui.database_clear"])

    def _send_request(self):
        if self.process is not None:
            self.process.write(self._request)
            self._request = b""
            self.process.closeWriteChannel()

    def _error(self, error):
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._finished(-1, QtCore.QProcess.ExitStatus.CrashExit)

    def _finished(self, exit_code, exit_status):
        if self.process is None:
            return
        try:
            reply = json.loads(bytes(self.process.readAllStandardOutput()).decode("utf-8"))
        except (ValueError, UnicodeError):
            reply = {}
        if not isinstance(reply, dict):
            reply = {}
        # Raw diagnostics are not displayed: they could contain credentials.
        self.process.readAllStandardError()
        self.process.deleteLater()
        self.process = None
        self._request = b""
        count = reply.get("deleted")
        success = (exit_code == 0 and exit_status == QtCore.QProcess.ExitStatus.NormalExit
                   and type(count) is int and count >= 0)
        message = redact_message(reply.get("error") or
            "Deletion was not confirmed as successful. Check the database contents before retrying.",
            (self._password,))
        self._password = ""
        if success:
            self.deleted_count = count
            super().accept()
        else:
            self.statusLabel.setText(message)
            self.passwordEdit.setEnabled(True)
            self.cancelButton.setEnabled(True)
            self.passwordEdit.setFocus()

    def accept(self):
        # Only the successful worker result may accept this dialog.
        pass

    def reject(self):
        if self.process is None:
            self.passwordEdit.clear()
            super().reject()

    def closeEvent(self, event):
        if self.process is not None:
            event.ignore()
        else:
            super().closeEvent(event)
