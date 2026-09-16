"""PyQt6 shell for the N=2 landscape database; run with python gui/n2_db.py."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import uuid

from PyQt6 import QtCore, QtWidgets, uic

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.number_utils import as_nonnegative_fraction
from gui.database_clear_dialog import DeleteDatabaseDialog

if __package__:
    from .anomaly_tab import AnomalyTabController
    from .index_tab import IndexTabController
    from .password_store import PasswordStorageError, PasswordVault
else:
    from anomaly_tab import AnomalyTabController
    from index_tab import IndexTabController
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
        "tools/tform_executable": "tform",
        "tools/form_threads": 1,
        "tools/timeout": 600.0,
        "tools/processes": -1,
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
    databaseContentsDeleted = QtCore.pyqtSignal(dict)

    def __init__(self, store: SettingsStore, parent=None):
        super().__init__(parent)
        uic.loadUi(str(GUI_DIRECTORY / "settings.ui"), self)
        self.store = store
        self._deletion_dialog = None
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
            "tools/tform_executable": self.tformEdit,
        }
        self.number_fields = {
            "index/full_max_order": self.fullIndexOrderSpin,
            "mysql/port": self.portSpin,
            "mysql/connect_timeout": self.connectTimeoutSpin,
            "tools/timeout": self.timeoutSpin,
            "tools/processes": self.coresSpin,
            "tools/form_threads": self.formThreadsSpin,
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
        self.tformBrowse.clicked.connect(lambda: self.browse_executable(self.tformEdit))
        self.deleteDatabaseContentsButton.clicked.connect(self.delete_database_contents)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

    @property
    def deletion_active(self):
        return self._deletion_dialog is not None and self._deletion_dialog.process is not None

    def delete_database_contents(self):
        parent = self.parent()
        if isinstance(parent, N2DatabaseWindow) and (
                parent.anomaly_tab.process is not None or parent.index_tab.process is not None):
            return
        if self._deletion_dialog is not None:
            return
        # Snapshot the displayed target. Do not reuse or save either password.
        settings = {key: field.text().strip() for key, field in self.text_fields.items()
                    if key.startswith("mysql/") and key != "mysql/password"}
        settings.update({key: field.value() for key, field in self.number_fields.items()
                         if key.startswith("mysql/")})
        settings["mysql/unix_socket"] = os.environ.get("N2_DB_UNIX_SOCKET")
        if any(not settings[key] for key in ("mysql/database", "mysql/host", "mysql/user")):
            QtWidgets.QMessageBox.warning(self, "Choose a database", "Fill in the database name, host and account first.")
            return
        dialog = DeleteDatabaseDialog(settings, self)
        self._deletion_dialog = dialog
        try:
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                self.databaseContentsDeleted.emit(settings)
                QtWidgets.QMessageBox.information(
                    self, "Database contents deleted",
                    f"Deleted {dialog.deleted_count} theories and all related data from "
                    f"{settings['mysql/database']}. The database is ready to use again.",
                )
        finally:
            self._deletion_dialog = None
            dialog.deleteLater()

    def reject(self):
        if not self.deletion_active:
            super().reject()

    def closeEvent(self, event):
        if self.deletion_active:
            event.ignore()
        else:
            super().closeEvent(event)

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
        if self.deletion_active:
            return
        values = {key: field.text() for key, field in self.text_fields.items()}
        values.update({key: field.value() for key, field in self.number_fields.items()})
        if values["tools/processes"] == 0:
            QtWidgets.QMessageBox.warning(
                self, "Invalid CPU core count",
                "Enter -1 to use all system cores, or a positive integer.",
            )
            self.coresSpin.setFocus()
            self.coresSpin.selectAll()
            return
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
            "tools/tform_executable",
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
        self._close_requested = False
        self._settings_dialog = None
        self.anomaly_tab = AnomalyTabController(self, self.store, project_root=PROJECT_ROOT)
        self.index_tab = IndexTabController(self, self.store, project_root=PROJECT_ROOT)
        self.anomaly_tab.activeChanged.connect(self._sync_tab_availability)
        self.index_tab.activeChanged.connect(self._sync_tab_availability)
        self._sync_tab_availability()

    def _sync_tab_availability(self, *_):
        anomaly_active = self.anomaly_tab.process is not None
        index_active = self.index_tab.process is not None
        self.anomaly_tab.set_available(not index_active)
        self.index_tab.set_available(not anomaly_active)
        self.actionSettings.setEnabled(not (anomaly_active or index_active))
        if self._close_requested and not (anomaly_active or index_active):
            self.close()

    def closeEvent(self, event):
        if self._settings_dialog is not None and self._settings_dialog.deletion_active:
            event.ignore()
            return
        if self.anomaly_tab.process is not None or self.index_tab.process is not None:
            self._close_requested = True
            self.anomaly_tab.stop()
            self.index_tab.cancel_for_close()
            event.ignore()
            return
        self.anomaly_tab.logger.close_file()
        self.index_tab.logger.close_file()
        super().closeEvent(event)

    @property
    def settings(self) -> dict[str, str | int | float]:
        """Current saved preferences, ready for future anomaly/index actions."""
        return self.store.load()

    def open_settings(self) -> None:
        if self.anomaly_tab.process is not None or self.index_tab.process is not None:
            return
        try:
            dialog = SettingsDialog(self.store, self)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot load settings", str(exc))
            return
        dialog.databaseContentsDeleted.connect(self.index_tab.invalidate_after_database_deletion)
        self._settings_dialog = dialog
        try:
            if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
                try:
                    self.index_tab.invalidate_if_database_changed(self.store.load())
                except OSError as exc:
                    QtWidgets.QMessageBox.critical(self, "Cannot load settings", str(exc))
        finally:
            self._settings_dialog = None
            dialog.deleteLater()


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
