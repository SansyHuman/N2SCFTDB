"""Asynchronous theory search, retained IDs and cancellable CSV downloads."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

from PyQt6 import QtCore, QtWidgets

from gui.index_tab import database_source
from gui.logging_utils import GuiLogger
from gui.theory_download import CSV_FIELDS


class SearchTabController(QtCore.QObject):
    activeChanged = QtCore.pyqtSignal(bool)

    def __init__(self, window, store, *, project_root=None):
        super().__init__(window)
        self.window = window
        self.store = store
        self.project_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
        self.process = None
        self._available = True
        self._cancelled = False
        self._theory_ids = ()
        self._database = None
        self._conditions = None
        self._request = b""
        self._mode = "search"
        self._last_download_directory = Path.home()
        self._fields = {
            "gauge_groups": window.searchGaugeGroupsEdit,
            "a": window.searchCentralChargeAEdit,
            "c": window.searchCentralChargeCEdit,
            "a_min": window.searchCentralChargeAMinEdit,
            "a_max": window.searchCentralChargeAMaxEdit,
            "c_min": window.searchCentralChargeCMinEdit,
            "c_max": window.searchCentralChargeCMaxEdit,
            "theory_id": window.searchTheoryIdEdit,
        }
        self.logger = GuiLogger(window.searchResultCountEdit.setToolTip)
        window.searchTheoriesButton.clicked.connect(self.search)
        window.downloadTheoriesButton.clicked.connect(self.download)
        for field in self._fields.values():
            field.textChanged.connect(self.invalidate)
        window.searchMinimumIndexOrderSpinBox.valueChanged.connect(self.invalidate)
        window.searchSingleSectorCheckBox.toggled.connect(self.invalidate)
        for field in CSV_FIELDS:
            getattr(window, field.widget).toggled.connect(self._sync_controls)
        self._sync_controls()

    @property
    def theory_ids(self):
        """Distinct IDs from the last complete search, ready for a later export."""
        return self._theory_ids

    @property
    def database(self):
        return deepcopy(self._database)

    @property
    def conditions(self):
        return deepcopy(self._conditions)

    @property
    def selected_csv_fields(self):
        return tuple(field.name for field in CSV_FIELDS if getattr(self.window, field.widget).isChecked())

    def _read_conditions(self):
        return {**{key: field.text().strip() for key, field in self._fields.items()},
                "minimum_index_order": self.window.searchMinimumIndexOrderSpinBox.value(),
                "only_single_sector": self.window.searchSingleSectorCheckBox.isChecked()}

    def invalidate(self, *_):
        self._theory_ids = ()
        self._database = self._conditions = None
        self.window.searchResultCountEdit.clear()
        self.window.searchResultCountEdit.setToolTip("")
        self.window.searchDownloadProgressBar.setValue(0)
        self._sync_controls()

    def invalidate_if_database_changed(self, settings):
        if self._database is not None and self._database != database_source(settings):
            self.invalidate()

    def invalidate_after_database_deletion(self, settings):
        self.invalidate()

    def set_available(self, available):
        if self._available and not available:
            # Another tab may change theories or index availability.
            self.invalidate()
        self._available = available
        self._sync_controls()

    def _sync_controls(self, *_):
        busy = self.process is not None
        downloading = busy and self._mode == "download"
        for field in self._fields.values():
            field.setEnabled(self._available and not busy)
        self.window.searchMinimumIndexOrderSpinBox.setEnabled(self._available and not busy)
        self.window.searchSingleSectorCheckBox.setEnabled(self._available and not busy)
        self.window.searchCsvFieldsGroup.setEnabled(self._available and not busy)
        self.window.searchTheoriesButton.setText(
            "Cancelling…" if busy and not downloading and self._cancelled
            else "Cancel" if busy and not downloading else "Search")
        self.window.searchTheoriesButton.setEnabled(
            busy and not downloading and not self._cancelled or self._available and not busy)
        self.window.downloadTheoriesButton.setText(
            "Cancelling…" if downloading and self._cancelled else "Cancel" if downloading else "Download")
        self.window.downloadTheoriesButton.setEnabled(
            downloading and not self._cancelled or self._available and not busy
            and bool(self._theory_ids) and bool(self.selected_csv_fields))

    def search(self):
        if self.process is not None:
            if self._mode == "search":
                self.cancel_for_close()
            return
        if not self._available:
            return
        self.invalidate()
        self._last_error = ""
        try:
            settings = self.store.load()
            self.logger.set_secrets(settings["mysql/password"])
            if not settings["mysql/database"].strip():
                raise ValueError("Set the MySQL database name in Settings → Preferences.")
            sibling = Path(sys.executable).with_name("sage")
            sage = str(sibling) if sibling.is_file() else shutil.which("sage")
            if sage is None:
                raise ValueError("Sage was not found. Launch from its Sage environment or put sage on PATH.")
            self._source = database_source(settings)
            self._search_conditions = self._read_conditions()
            connection_settings = {key: value for key, value in settings.items() if key.startswith("mysql/")}
            connection_settings["mysql/unix_socket"] = self._source["unix_socket"]
            self._request = (json.dumps({"settings": connection_settings,
                                         "conditions": self._search_conditions}) + "\n").encode("utf-8")
        except (OSError, ValueError) as exc:
            self.logger.log(f"Cannot search: {exc}", "ERROR")
            self.window.searchResultCountEdit.setText("Cannot search. See the error in the tooltip.")
            self.logger.set_secrets()
            return
        self.logger.start_file(self.project_root / "logs", prefix="log_search", label="theory search log")
        self._launch(sage, "gui.theory_search", "search")

    def download(self):
        if self.process is not None:
            if self._mode == "download":
                self.cancel_for_close()
            return
        if not self._available or not self._theory_ids:
            return
        try:
            fields = self.selected_csv_fields
            if not fields:
                raise ValueError("Select at least one CSV field.")
            settings = self.store.load()
            self.logger.set_secrets(settings["mysql/password"])
            if self._database != database_source(settings):
                self.invalidate()
                raise ValueError("Database settings changed. Search again before downloading.")
            sibling = Path(sys.executable).with_name("sage")
            sage = str(sibling) if sibling.is_file() else shutil.which("sage")
            if sage is None:
                raise ValueError("Sage was not found. Launch from its Sage environment or put sage on PATH.")
            dialog = QtWidgets.QFileDialog(self.window, "Save searched theories",
                                          str(self._last_download_directory), "CSV files (*.csv)")
            dialog.setAcceptMode(QtWidgets.QFileDialog.AcceptMode.AcceptSave)
            dialog.setDefaultSuffix("csv")
            dialog.selectFile("theories.csv")
            try:
                if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                    self.logger.set_secrets()
                    return
                self._download_path = str(Path(dialog.selectedFiles()[0]).absolute())
            finally:
                dialog.deleteLater()
            self._last_download_directory = Path(self._download_path).parent
            export_settings = {key: value for key, value in settings.items() if key.startswith("mysql/")}
            export_settings["mysql/unix_socket"] = self._database["unix_socket"]
            export_settings["tools/processes"] = settings.get("tools/processes", -1)
            self._request = (json.dumps({"settings": export_settings, "theory_ids": self._theory_ids,
                                         "fields": fields, "destination": self._download_path}) + "\n").encode("utf-8")
        except (OSError, ValueError) as exc:
            self.logger.log(f"Cannot download: {exc}", "ERROR")
            self.window.searchResultCountEdit.setText("Cannot download. See the error in the tooltip.")
            self.logger.set_secrets()
            return
        self.logger.start_file(self.project_root / "logs", prefix="log_download", label="CSV download log")
        self._last_error = ""
        self._download_written = 0
        self._download_total = len(self._theory_ids)
        self._download_complete = None
        self._download_cancelled = False
        self.window.searchDownloadProgressBar.setValue(0)
        self._launch(sage, "gui.theory_download", "download")

    def _launch(self, sage, module, mode):
        self._mode = mode
        self._pending = []
        self._complete = None
        self._protocol_error = False
        self._cancelled = False
        self._stdout = self._stderr = b""
        process = QtCore.QProcess(self)
        self.process = process
        process.setWorkingDirectory(str(self.project_root))
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.SeparateChannels)
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONDONTWRITEBYTECODE", "1")
        process.setProcessEnvironment(environment)
        process.started.connect(self._send_request)
        process.readyReadStandardOutput.connect(self._read_output)
        process.readyReadStandardError.connect(lambda: self._read_output(stderr=True))
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        self.window.searchResultCountEdit.setText(
            "Searching…" if mode == "search" else f"Writing 0/{len(self._theory_ids)} theories…")
        self._sync_controls()
        self.activeChanged.emit(True)
        process.start(sage, ["-python", "-B", "-u", "-m", module])

    def _send_request(self):
        if self.process is not None:
            self.process.write(self._request)
            self._request = b""
            if self._mode == "search":
                self.process.closeWriteChannel()

    def _read_output(self, final=False, *, stderr=False):
        if self.process is None:
            return
        name = "_stderr" if stderr else "_stdout"
        read = self.process.readAllStandardError if stderr else self.process.readAllStandardOutput
        lines = (getattr(self, name) + bytes(read())).split(b"\n")
        remaining = lines.pop()
        if final and remaining:
            lines.append(remaining)
            remaining = b""
        setattr(self, name, remaining)
        for line in lines:
            text = line.decode("utf-8", errors="replace")
            if stderr:
                self.logger.log(text, "WARNING")
                continue
            try:
                record = json.loads(text)
                if not isinstance(record, dict):
                    raise ValueError("expected a record")
                if self._mode == "download" and "log" not in record:
                    self._accept_download_record(record)
                elif self._mode == "search" and set(record) == {"theory_id"}:
                    theory_id = record["theory_id"]
                    if (type(theory_id) is not int or not 1 <= theory_id <= 2**64 - 1
                            or self._complete is not None
                            or self._pending and theory_id <= self._pending[-1]):
                        raise ValueError("invalid, duplicate or out-of-order theory")
                    self._pending.append(theory_id)
                elif self._mode == "search" and set(record) == {"complete"}:
                    if (self._complete is not None or type(record["complete"]) is not int
                            or record["complete"] != len(self._pending)):
                        raise ValueError("invalid completion count")
                    self._complete = record["complete"]
                elif "log" in record and isinstance(record["log"], str):
                    level = record.get("level", "INFO")
                    self.logger.log(record["log"], level, record.get("timestamp"))
                    if level in {"ERROR", "CRITICAL"}:
                        self._last_error = self.window.searchResultCountEdit.toolTip()
                        self._protocol_error = True
                else:
                    raise ValueError("unrecognized worker response")
            except (ValueError, TypeError, KeyError):
                self._protocol_error = True
                self.logger.log("Invalid worker response; completion cannot be confirmed.", "ERROR")

    def _accept_download_record(self, record):
        if self._download_complete is not None or self._download_cancelled:
            raise ValueError("response after download completion")
        if set(record) == {"download_cancelled"} and record["download_cancelled"] is True:
            self._download_cancelled = True
            return
        if set(record) not in ({"download_progress"}, {"download_complete"}):
            raise ValueError("invalid download response")
        complete = "download_complete" in record
        value = record["download_complete" if complete else "download_progress"]
        written, total = value["written"], value["total"]
        if (type(written) is not int or type(total) is not int or total != self._download_total
                or not self._download_written <= written <= total or written < 0):
            raise ValueError("invalid download counts")
        if complete:
            if (written != total or value["path"] != self._download_path
                    or type(value["rows"]) is not int or value["rows"] < total):
                raise ValueError("invalid download completion")
            self._download_complete = value
        elif written == total:
            raise ValueError("100% requires a completed file")
        self._download_written = written
        # Completion is displayed only after a successful process exit as well.
        if not complete:
            self.window.searchDownloadProgressBar.setValue(100 * written // total)
            self.window.searchResultCountEdit.setText(f"Writing {written}/{total} theories…")

    def _error(self, error):
        if self.process is None:
            return
        self.logger.log(f"{self._mode.capitalize()} process: {self.process.errorString()}", "ERROR")
        self._last_error = self.window.searchResultCountEdit.toolTip()
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._finished(-1, QtCore.QProcess.ExitStatus.CrashExit)

    def _finished(self, exit_code, exit_status):
        if self.process is None:
            return
        self._read_output(final=True)
        self._read_output(final=True, stderr=True)
        if self._mode == "download":
            success = (not self._protocol_error and exit_code == 0
                       and exit_status == QtCore.QProcess.ExitStatus.NormalExit
                       and self._download_complete is not None)
            if success:
                # A confirmed atomic save wins a late cancellation request.
                self.window.searchDownloadProgressBar.setValue(100)
                message = f"Saved {self._download_total} theories ({self._download_complete['rows']} CSV rows)"
                self.logger.log(f"{message} to {self._download_path}.")
            elif self._download_cancelled:
                message = "Download cancelled; searched theories retained"
                self.logger.log(message)
            else:
                message = "Download failed or completion unconfirmed; searched theories retained"
                self.logger.log(message, "ERROR")
            self.window.searchResultCountEdit.setText(message)
            self._release_process(success)
            return
        success = (not self._cancelled and not self._protocol_error and exit_code == 0
                   and exit_status == QtCore.QProcess.ExitStatus.NormalExit
                   and self._complete == len(self._pending)
                   and self._search_conditions == self._read_conditions())
        if success:
            self._theory_ids = tuple(self._pending)
            self._database = self._source
            self._conditions = self._search_conditions
            count = len(self._theory_ids)
            message = f"{count} {'theory' if count == 1 else 'theories'} found"
            self.logger.log(message)
        else:
            message = "Search cancelled" if self._cancelled else "Search failed; no results retained"
            self.logger.log(message, "INFO" if self._cancelled else "ERROR")
        self.window.searchResultCountEdit.setText(message)
        self._release_process(success)

    def _release_process(self, success):
        if not success and self._last_error:
            self.window.searchResultCountEdit.setToolTip(self._last_error)
        self.process.deleteLater()
        self.process = None
        self._pending = []
        self._request = self._stdout = self._stderr = b""
        self.logger.close_file()
        self.logger.set_secrets()
        self._sync_controls()
        self.activeChanged.emit(False)

    def cancel_for_close(self):
        if self.process is not None and not self._cancelled:
            self._cancelled = True
            if self._mode == "download":
                self.logger.log("Cancelling the CSV download; waiting for readers and temporary-file cleanup.")
                if self._request:
                    self._request += b"stop\n"
                else:
                    self.process.write(b"stop\n")
            else:
                self.logger.log("Cancelling the read-only theory search.")
                self.process.kill()
            self._sync_controls()
