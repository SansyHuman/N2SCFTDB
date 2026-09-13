"""PyQt6 shell for the N=2 landscape database; run with python gui/n2_db.py."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
import json
import shutil
from pathlib import Path
import sys
import tempfile
import uuid

from PyQt6 import QtCore, QtGui, QtWidgets, uic

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.number_utils import as_nonnegative_fraction

if __package__:
    from .password_store import PasswordStorageError, PasswordVault
else:
    from password_store import PasswordStorageError, PasswordVault


GUI_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = GUI_DIRECTORY.parent


def default_settings() -> dict[str, str | int | float]:
    """Mirror backend defaults without importing Sage or opening any database.

    Sources: index/{char_decomposition_cache,form_expansion_cache,n2_theory_index}
    and common/{n2_theory_db,n2_theory_properties}.py.
    Environment overrides match the MySQL CLI.
    A database name is required by that API and has no project default.
    """
    return {
        "cache/character_database": str(PROJECT_ROOT / "char_decomposition_cache.db"),
        "cache/form_database": str(PROJECT_ROOT / "form_expansion_cache.db"),
        "index/full_max_order": 18,
        # Exact text is accepted by the backend, including rational dimensions.
        "index/coulomb_max_dimension": "90",
        "mysql/database": "",
        "mysql/host": os.environ.get("N2_DB_HOST", "127.0.0.1"),
        "mysql/port": 3306,
        "mysql/user": os.environ.get("N2_DB_USER", "root"),
        "mysql/password": os.environ.get("N2_DB_PASSWORD", ""),
        "mysql/connect_timeout": 10,
        "tools/lie_executable": "lie",
        "tools/form_executable": "form",
        "tools/timeout": 600.0,
    }


class SettingsStore:
    """Save per-user preferences independently of the current working directory."""

    def __init__(self, filename: str | Path | None = None, *, vault=None):
        if filename is None:
            config_root = QtCore.QStandardPaths.writableLocation(
                QtCore.QStandardPaths.StandardLocation.GenericConfigLocation
            )
            # Stable storage identity: keep preferences across the N2SCFTDB rename.
            filename = Path(config_root) / "SuperconformalIndex" / "n2_db.ini"
        self.path = Path(filename).expanduser().resolve()
        self.vault = vault if vault is not None else PasswordVault()

    def _read_preferences(self):
        settings = QtCore.QSettings(str(self.path), QtCore.QSettings.Format.IniFormat)
        settings.sync()
        if settings.status() != QtCore.QSettings.Status.NoError:
            raise OSError(f"Could not read settings from {self.path}")
        return settings

    def migrate_legacy_password(self) -> None:
        """Upgrade a previous plaintext setting at startup, without logging it."""
        if self._read_preferences().contains("mysql/password"):
            self.load()

    def load(self) -> dict[str, str | int | float]:
        settings = self._read_preferences()
        values = default_settings()
        for key, default in values.items():
            if key == "mysql/password":
                continue
            try:
                values[key] = settings.value(key, default, type=type(default))
            except (TypeError, ValueError):
                values[key] = default
        if PROJECT_ROOT.name == "N2SCFTDB":
            previous_root = PROJECT_ROOT.with_name("SuperconformalIndex")
            for key in ("cache/character_database", "cache/form_database"):
                try:
                    relative = Path(values[key]).relative_to(previous_root)
                except ValueError:
                    continue
                values[key] = str(PROJECT_ROOT / relative)
        if settings.contains("mysql/password_id"):
            credential_id = settings.value("mysql/password_id", type=str)
            # An empty reference means an explicitly saved empty password, which
            # must override N2_DB_PASSWORD without requiring access to a vault.
            values["mysql/password"] = self.vault.get(credential_id) if credential_id else ""
            if settings.contains("mysql/password"):
                self.save(values)
        elif settings.contains("mysql/password"):
            values["mysql/password"] = settings.value("mysql/password", type=str)
            # Save verifies the secret in the vault before removing plaintext.
            # On any failure the original settings remain available for retry.
            self.save(values)
        return values

    def save(self, values: dict[str, str | int | float]) -> None:
        password = values["mysql/password"]
        if not isinstance(password, str):
            raise TypeError("Password must be a string")
        previous = self._read_preferences()
        old_id = previous.value("mysql/password_id", "", type=str)
        new_id = uuid.uuid4().hex if password else ""
        try:
            if new_id:
                self.vault.set(new_id, password)
            self._write_preferences(values, new_id, previous)
        except Exception:
            self._discard_credential(new_id)
            raise
        # A fresh ID prevents a failed INI write from changing the credential
        # referenced by the old settings. Remove the old entry only after commit.
        self._discard_credential(old_id)

    def _discard_credential(self, credential_id):
        if credential_id:
            try:
                self.vault.delete(credential_id)
            except PasswordStorageError:
                # An unreachable vault can leave an unused, still encrypted entry.
                # The newly committed settings must remain usable in this case.
                pass

    def _write_preferences(self, values, credential_id, previous):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Render a password-free INI first. Atomic replacement avoids QSettings
        # flushing pending changes after a reported write failure. No plaintext
        # backup of the old file is made, including during migration.
        with tempfile.TemporaryDirectory(prefix=".n2-settings-", dir=self.path.parent) as directory:
            temporary_path = Path(directory) / "settings.ini"
            settings = QtCore.QSettings(str(temporary_path), QtCore.QSettings.Format.IniFormat)
            for key in previous.allKeys():
                if key not in ("mysql/password", "mysql/password_id"):
                    settings.setValue(key, previous.value(key))
            for key in default_settings():
                if key != "mysql/password":
                    settings.setValue(key, values[key])
            settings.setValue("mysql/password_id", credential_id)
            settings.sync()
            if settings.status() != QtCore.QSettings.Status.NoError:
                raise OSError("Could not prepare settings for saving")
            payload = temporary_path.read_bytes()
            destination = QtCore.QSaveFile(str(self.path))
            if not destination.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(f"Could not save settings to {self.path}")
            permissions = (
                QtCore.QFileDevice.Permission.ReadOwner | QtCore.QFileDevice.Permission.WriteOwner
            )
            if not destination.setPermissions(permissions) or destination.write(payload) != len(payload):
                destination.cancelWriting()
                raise OSError(f"Could not save settings to {self.path}")
            if not destination.commit():
                raise OSError(f"Could not save settings to {self.path}")


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, store: SettingsStore, parent=None):
        super().__init__(parent)
        uic.loadUi(str(GUI_DIRECTORY / "settings.ui"), self)
        self.store = store
        self.text_fields = {
            "cache/character_database": self.characterCacheEdit,
            "cache/form_database": self.formCacheEdit,
            "index/coulomb_max_dimension": self.coulombMaxDimensionEdit,
            "mysql/database": self.databaseEdit,
            "mysql/host": self.hostEdit,
            "mysql/user": self.userEdit,
            "mysql/password": self.passwordEdit,
            "tools/lie_executable": self.lieEdit,
            "tools/form_executable": self.formEdit,
        }
        self.number_fields = {
            "index/full_max_order": self.fullIndexOrderSpin,
            "mysql/port": self.portSpin,
            "mysql/connect_timeout": self.connectTimeoutSpin,
            "tools/timeout": self.timeoutSpin,
        }
        values = store.load()
        for key, field in self.text_fields.items():
            field.setText(values[key])
        for key, field in self.number_fields.items():
            field.setValue(values[key])
        # Long absolute cache paths remain editable; show the filename initially.
        for field in (self.characterCacheEdit, self.formCacheEdit):
            field.setCursorPosition(len(field.text()))
        self.characterCacheBrowse.clicked.connect(
            lambda: self.browse_cache(self.characterCacheEdit)
        )
        self.formCacheBrowse.clicked.connect(lambda: self.browse_cache(self.formCacheEdit))
        self.lieBrowse.clicked.connect(lambda: self.browse_executable(self.lieEdit))
        self.formBrowse.clicked.connect(lambda: self.browse_executable(self.formEdit))
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

    def browse_cache(self, field: QtWidgets.QLineEdit) -> None:
        # Selection only: the chosen database may be existing or not yet created.
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Choose cache database", field.text(),
            "SQLite databases (*.db *.sqlite *.sqlite3);;All files (*)",
            options=QtWidgets.QFileDialog.Option.DontConfirmOverwrite,
        )
        if filename:
            field.setText(filename)

    def browse_executable(self, field: QtWidgets.QLineEdit) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose executable", field.text(), "All files (*)"
        )
        if filename:
            field.setText(filename)

    def accept(self) -> None:
        values = {key: field.text() for key, field in self.text_fields.items()}
        values.update({key: field.value() for key, field in self.number_fields.items()})
        try:
            values["index/coulomb_max_dimension"] = str(as_nonnegative_fraction(
                values["index/coulomb_max_dimension"], "Coulomb index maximum dimension"
            ))
        except ValueError:
            QtWidgets.QMessageBox.warning(
                self, "Invalid Coulomb cutoff",
                "Enter a nonnegative integer or fraction, such as 90 or 6/5.",
            )
            self.coulombMaxDimensionEdit.setFocus()
            self.coulombMaxDimensionEdit.selectAll()
            return
        required = (
            "cache/character_database", "cache/form_database", "mysql/host",
            "mysql/user", "tools/lie_executable", "tools/form_executable",
        )
        for key in required:
            values[key] = values[key].strip()
            if not values[key]:
                QtWidgets.QMessageBox.warning(
                    self, "Missing setting", "Please fill in the highlighted field."
                )
                self.text_fields[key].setFocus()
                return
        # Anchor relative cache paths to the checkout, not the launch directory.
        for key in ("cache/character_database", "cache/form_database"):
            path = Path(values[key]).expanduser()
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            values[key] = str(path.resolve())
        # Never trim passwords: whitespace can be part of the credential.
        try:
            self.store.save(values)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Settings not saved", str(exc))
            return
        super().accept()


class N2DatabaseWindow(QtWidgets.QMainWindow):
    def __init__(self, store: SettingsStore | None = None):
        super().__init__()
        uic.loadUi(str(GUI_DIRECTORY / "n2_db.ui"), self)
        self.store = store if store is not None else SettingsStore()
        self.actionSettings.triggered.connect(self.open_settings)
        self.actionQuit.triggered.connect(self.close)
        self.loadTheoriesButton.clicked.connect(self.load_theories)
        self.buildTheoriesButton.clicked.connect(self.build_theories)
        self._build_process = None
        self._build_buffer = b""
        self._build_error_buffer = b""
        self._build_password = ""
        self._build_request = b""
        self._close_after_build = False
        self._anomaly_log = None
        self._anomaly_log_path = None

    def _start_anomaly_log(self):
        self._close_anomaly_log()
        directory = PROJECT_ROOT / "logs"
        self._anomaly_log_path = directory
        try:
            directory.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now()
            while True:
                self._anomaly_log_path = directory / f"log_anomalies_{timestamp:%Y%m%d_%H%M%S_%f}.log"
                try:
                    self._anomaly_log = self._anomaly_log_path.open(
                        "x", encoding="utf-8", newline="\n",
                    )
                    break
                except FileExistsError:
                    # Concurrent windows or repeated timestamps must not overwrite logs.
                    timestamp += timedelta(microseconds=1)
        except OSError as exc:
            self._log_build(f"Cannot create anomaly log at {self._anomaly_log_path}: {exc}", level="ERROR")
            return
        self._log_build(f"Saving anomaly log to {self._anomaly_log_path}")

    def _close_anomaly_log(self):
        stream, self._anomaly_log = self._anomaly_log, None
        if stream is not None:
            try:
                stream.close()
            except OSError as exc:
                self._log_build(f"Cannot close anomaly log at {self._anomaly_log_path}: {exc}", level="ERROR")

    def _log_build(self, message, level="INFO", timestamp=None):
        message = str(message)
        if self._build_password:
            message = message.replace(self._build_password, "[redacted]")
        level = str(level).upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            level = "INFO"
        try:
            moment = datetime.fromisoformat(timestamp) if timestamp else datetime.now().astimezone()
        except (TypeError, ValueError):
            moment = datetime.now().astimezone()
        stamp = moment.astimezone().isoformat(sep=" ", timespec="milliseconds")
        rendered = "\n".join(f"[{stamp}] [{level}] {line}" for line in (message.splitlines() or [""]))
        self.theoryBuildLog.appendPlainText(rendered)
        if self._anomaly_log is not None:
            try:
                self._anomaly_log.write(rendered + "\n")
                self._anomaly_log.flush()
            except (OSError, UnicodeError) as exc:
                self._close_anomaly_log()
                self._log_build(f"Cannot write anomaly log at {self._anomaly_log_path}: {exc}", level="ERROR")

    def _set_build_active(self, active):
        self.theoriesInput.setReadOnly(active)
        self.loadTheoriesButton.setEnabled(not active)
        self.buildCharacterCacheCheckBox.setEnabled(not active)
        self.actionSettings.setEnabled(not active)
        self.buildTheoriesButton.setText("Stop" if active else "Build")
        self.buildTheoriesButton.setEnabled(True)

    def build_theories(self):
        if self._build_process is not None:
            self._stop_build()
            return
        self._start_anomaly_log()
        text = self.theoriesInput.toPlainText()
        if not text.strip():
            self._log_build("Enter at least one gauge group in the left area.", level="ERROR")
            self._close_anomaly_log()
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
            self._log_build(f"Cannot start build: {exc}", level="ERROR")
            self._close_anomaly_log()
            return
        self._build_password = settings["mysql/password"]
        self._build_request = (json.dumps({
            "text": text, "settings": settings,
            "build_cache": self.buildCharacterCacheCheckBox.isChecked(),
        }) + "\n").encode("utf-8")
        self._build_buffer = b""
        self._build_error_buffer = b""
        process = QtCore.QProcess(self)
        self._build_process = process
        process.setWorkingDirectory(str(PROJECT_ROOT))
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
        self._log_build("Starting theory build with the saved settings…")
        # A separate main thread is needed for Sage and its spawned cache workers.
        # Credentials travel through stdin, never command arguments or a file.
        process.start(sage, ["-python", "-B", "-u", "-m", "gui.theory_builder"])

    def _send_build_request(self):
        if self._build_process is not None:
            self._build_process.write(self._build_request)
            self._build_request = b""

    def _read_build_output(self, final=False, *, stderr=False):
        if self._build_process is None:
            return
        buffer_name = "_build_error_buffer" if stderr else "_build_buffer"
        read = (self._build_process.readAllStandardError if stderr
                else self._build_process.readAllStandardOutput)
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
                self._log_build(record["log"], level=record.get("level", "INFO"),
                                timestamp=record.get("timestamp"))
            else:
                # Unstructured diagnostics have no declared severity.
                self._log_build(text, level="WARNING" if stderr else "INFO")

    def _stop_build(self):
        if not self.buildTheoriesButton.isEnabled():
            return
        # The worker stops between candidates or after a committed cache order.
        # Keep its pipe open until it exits so it can finish the current operation.
        if self._build_request:
            self._build_request += b"stop\n"
        else:
            self._build_process.write(b"stop\n")
        self.buildTheoriesButton.setEnabled(False)
        self._log_build("Stop requested; waiting for the current operation to finish…")

    def _build_error(self, error):
        if self._build_process is None:
            return
        self._log_build(f"Build process: {self._build_process.errorString()}", level="ERROR")
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._build_finished(-1, QtCore.QProcess.ExitStatus.CrashExit)

    def _build_finished(self, exit_code, exit_status):
        if self._build_process is None:
            return
        self._read_build_output(final=True)
        self._read_build_output(final=True, stderr=True)
        if exit_status == QtCore.QProcess.ExitStatus.CrashExit or exit_code != 0:
            self._log_build(f"Build worker exited with code {exit_code}; see error details above.", level="ERROR")
        self._build_process.deleteLater()
        self._build_process = None
        self._build_request = b""
        self._close_anomaly_log()
        self._build_password = ""
        self._set_build_active(False)
        if self._close_after_build:
            self.close()

    def closeEvent(self, event):
        if self._build_process is not None:
            self._close_after_build = True
            self._stop_build()
            event.ignore()
            return
        self._close_anomaly_log()
        super().closeEvent(event)

    def load_theories(self) -> None:
        """Append selected text files as one undoable edit, after all reads succeed."""
        filenames, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Load theories", str(PROJECT_ROOT),
            "Text files (*.txt);;All files (*)",
        )
        parts = []
        for filename in filenames:
            try:
                # Accept an optional UTF-8 BOM and normalize platform line endings.
                text = Path(filename).read_text(encoding="utf-8-sig")
            except UnicodeError:
                QtWidgets.QMessageBox.critical(
                    self, "Cannot load theories",
                    f"{filename} is not valid UTF-8 text.\n"
                    "Save the file as UTF-8 and try again.",
                )
                return
            except OSError as exc:
                QtWidgets.QMessageBox.critical(
                    self, "Cannot load theories", f"Could not read {filename}:\n{exc}",
                )
                return
            if text:
                if parts and not parts[-1].endswith("\n"):
                    parts.append("\n")
                parts.append(text)
        if not parts:
            return
        existing = self.theoriesInput.toPlainText()
        separator = "\n" if existing and not existing.endswith("\n") else ""
        cursor = self.theoriesInput.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        cursor.insertText(separator + "".join(parts))
        cursor.endEditBlock()
        self.theoriesInput.setTextCursor(cursor)
        self.theoriesInput.setFocus()
        self.theoriesInput.ensureCursorVisible()

    @property
    def settings(self) -> dict[str, str | int | float]:
        """Current saved preferences, ready for future anomaly/index actions."""
        return self.store.load()

    def open_settings(self) -> None:
        try:
            dialog = SettingsDialog(self.store, self)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot load settings", str(exc))
            return
        dialog.exec()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setOrganizationName("N2SCFTDB")
    app.setApplicationName("N2Database")
    window = N2DatabaseWindow()
    window.show()
    try:
        window.store.migrate_legacy_password()
    except OSError as exc:
        QtWidgets.QMessageBox.critical(window, "Password migration incomplete", str(exc))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
