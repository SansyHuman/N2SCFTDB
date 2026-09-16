"""Index search, retained selections and asynchronous calculation actions."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys

from PyQt6 import QtCore, QtWidgets

from gui.logging_utils import GuiLogger


def database_source(settings):
    """Identify the database without retaining its password in the result cache."""
    return {
        "database": settings["mysql/database"].strip(),
        "host": settings["mysql/host"], "port": settings["mysql/port"],
        "user": settings["mysql/user"],
        "unix_socket": settings.get("mysql/unix_socket", os.environ.get("N2_DB_UNIX_SOCKET")),
    }


class IndexTabController(QtCore.QObject):
    activeChanged = QtCore.pyqtSignal(bool)

    def __init__(self, window, store, *, project_root=None):
        super().__init__(window)
        self.window = window
        self.store = store
        self.project_root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[1]
        self.process = None
        self._groups = {}
        self._database = None
        self._available = True
        self.logger = GuiLogger(window.indexCalculationLog.appendPlainText)
        self._request = b""
        self._mode = "search"
        self._cancelled = False
        window.searchEmptyIndicesButton.clicked.connect(self.search)
        window.calculateIndexButton.clicked.connect(self.calculate)
        window.selectAllIndexGroupsCheckBox.toggled.connect(self.select_all)
        window.emptyIndexGaugeGroupsList.itemChanged.connect(self._sync_select_all)
        window.emptyIndexGaugeGroupsList.viewport().installEventFilter(self)
        self._sync_select_all()

    def set_available(self, available):
        """Let the main window prevent a search while another tab is busy."""
        self._available = available
        self._sync_controls()

    def _sync_controls(self):
        idle = self.process is None
        listing = self.window.emptyIndexGaugeGroupsList
        selected = any(listing.item(i).checkState() == QtCore.Qt.CheckState.Checked
                       for i in range(listing.count()))
        calculating = not idle and self._mode == "calculation"
        self.window.searchEmptyIndicesButton.setEnabled(self._available and idle)
        listing.setEnabled(self._available and idle)
        self.window.selectAllIndexGroupsCheckBox.setEnabled(self._available and idle and listing.count() > 0)
        self.window.calculateIndexButton.setText("Stopping…" if calculating and self._cancelled
                                                else "Stop" if calculating else "Calculate index")
        self.window.calculateIndexButton.setEnabled(
            calculating and not self._cancelled or self._available and idle and selected,
        )

    def eventFilter(self, watched, event):
        if (event.type() == QtCore.QEvent.Type.MouseButtonPress
                and event.button() == QtCore.Qt.MouseButton.LeftButton):
            # Qt's checkbox delegate consumes the click before moving the current
            # row. Keep keyboard Space/arrow navigation on the clicked group.
            listing = self.window.emptyIndexGaugeGroupsList
            item = listing.itemAt(event.position().toPoint())
            if item is not None:
                listing.setCurrentItem(item)
        return super().eventFilter(watched, event)

    @property
    def database(self):
        return deepcopy(self._database)

    @property
    def retrieved_jobs(self):
        """Complete retained inputs/IDs/missing fields; no database query is made."""
        return tuple(deepcopy(job) for group in self._groups.values() for job in group["jobs"])

    @property
    def selected_jobs(self):
        """Jobs for checked groups, ready for calculation without a new search."""
        listing = self.window.emptyIndexGaugeGroupsList
        keys = [tuple(listing.item(i).data(QtCore.Qt.ItemDataRole.UserRole))
                for i in range(listing.count())
                if listing.item(i).checkState() == QtCore.Qt.CheckState.Checked]
        return tuple(deepcopy(job) for key in keys for job in self._groups[key]["jobs"])

    def invalidate_if_database_changed(self, settings):
        if self._database is not None and self._database != database_source(settings):
            self._groups = {}
            self._database = None
            self.window.emptyIndexGaugeGroupsList.clear()
            self._sync_select_all()
            self.log("Database settings changed; cleared the previous search results.")

    def invalidate_after_database_deletion(self, settings):
        # Clear even when another account/host spelling selected the same DB.
        # This also runs when Settings is cancelled after a successful deletion.
        self._groups = {}
        self._database = None
        self.window.emptyIndexGaugeGroupsList.clear()
        self._sync_select_all()
        self.log("Database contents deleted; cleared the previous search results.")

    def select_all(self, checked):
        listing = self.window.emptyIndexGaugeGroupsList
        blocker = QtCore.QSignalBlocker(listing)
        state = QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
        for i in range(listing.count()):
            listing.item(i).setCheckState(state)
        del blocker
        self._sync_select_all()

    def _sync_select_all(self, *_):
        listing = self.window.emptyIndexGaugeGroupsList
        checked = listing.count() > 0 and all(
            listing.item(i).checkState() == QtCore.Qt.CheckState.Checked
            for i in range(listing.count())
        )
        control = self.window.selectAllIndexGroupsCheckBox
        blocker = QtCore.QSignalBlocker(control)
        control.setChecked(checked)
        del blocker
        self._sync_controls()

    def log(self, message, level="INFO", timestamp=None):
        self.logger.log(message, level, timestamp)

    def search(self):
        if self.process is not None or not self._available:
            return
        self.logger.start_file(
            self.project_root / "logs", prefix="log_index", label="index search log",
        )
        try:
            settings = self.store.load()
            self.logger.set_secrets(settings["mysql/password"])
            self.invalidate_if_database_changed(settings)
            if not settings["mysql/database"].strip():
                raise ValueError("Set the MySQL database name in Settings → Preferences.")
            sibling = Path(sys.executable).with_name("sage")
            sage = str(sibling) if sibling.is_file() else shutil.which("sage")
            if sage is None:
                raise ValueError("Sage was not found. Launch from its Sage environment or put sage on PATH.")
        except (OSError, ValueError) as exc:
            self.log(f"Cannot search: {exc}", "ERROR")
            self.logger.close_file()
            self.logger.set_secrets()
            return
        self._source = database_source(settings)
        # Only connection fields cross the pipe; no credentials go in argv/files.
        connection_settings = {key: value for key, value in settings.items() if key.startswith("mysql/")}
        connection_settings["mysql/unix_socket"] = self._source["unix_socket"]
        self._request = (json.dumps({"settings": connection_settings}) + "\n").encode("utf-8")
        self._pending = {}
        self._seen_ids = set()
        self._complete = None
        self._mode = "search"
        self._launch(sage, "gui.index_search")

    def calculate(self):
        if self.process is not None:
            if self._mode == "calculation":
                self.stop()
            return
        if not self._available:
            return
        self.logger.start_file(self.project_root / "logs", prefix="log_index", label="index calculation log")
        try:
            settings = self.store.load()
            self.logger.set_secrets(settings["mysql/password"])
            self.invalidate_if_database_changed(settings)
            jobs = self.selected_jobs
            if not jobs:
                raise ValueError("Select at least one gauge group with missing indices.")
            sibling = Path(sys.executable).with_name("sage")
            sage = str(sibling) if sibling.is_file() else shutil.which("sage")
            if sage is None:
                raise ValueError("Sage was not found. Launch from its Sage environment or put sage on PATH.")
            settings = dict(settings, **{"mysql/unix_socket": self._database["unix_socket"]})
            self._request = (json.dumps({"settings": settings, "jobs": jobs}) + "\n").encode("utf-8")
        except (OSError, ValueError) as exc:
            self.log(f"Cannot calculate indices: {exc}", "ERROR")
            self.logger.close_file()
            self.logger.set_secrets()
            return
        self._mode = "calculation"
        self._calculation_jobs = {job["theory_id"]: job for job in jobs}
        self._results = {}
        self._calculation_complete = None
        self._launch(sage, "gui.index_calculator")

    def _launch(self, sage, module):
        self._protocol_error = False
        self._cancelled = False
        self._stdout = b""
        self._stderr = b""
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
        self._sync_select_all()
        self.activeChanged.emit(True)
        self.log("Starting search with the saved database settings…" if self._mode == "search" else
                 f"Starting index calculation for {len(self._calculation_jobs)} selected theories…")
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
                self.log(text, "WARNING")
                continue
            try:
                record = json.loads(text)
            except ValueError:
                self.log(text)
                continue
            try:
                if "result" in record and self._mode == "calculation":
                    self._accept_result(record["result"])
                elif "calculation_complete" in record and self._mode == "calculation":
                    summary = record["calculation_complete"]
                    if (self._calculation_complete is not None or summary["processed"] != len(self._results)
                            or summary["total"] != len(self._calculation_jobs)
                            or summary["status"] not in ("completed", "completed with errors", "stopped", "failed")
                            or (summary["status"].startswith("completed")
                                and summary["processed"] != summary["total"])):
                        raise ValueError("invalid calculation completion")
                    self._calculation_complete = summary
                elif "job" in record and self._mode == "search":
                    job, group = record["job"], record["group"]
                    key = tuple(group["algebras"])
                    theory_id = job["theory_id"]
                    if not key or theory_id in self._seen_ids or self._complete is not None:
                        raise ValueError("duplicate or out-of-order job")
                    self._seen_ids.add(theory_id)
                    entry = self._pending.setdefault(key, {"label": group["label"], "jobs": []})
                    entry["jobs"].append(job)
                elif "complete" in record and self._mode == "search":
                    if self._complete is not None or type(record["complete"]) is not int:
                        raise ValueError("invalid completion marker")
                    self._complete = record["complete"]
                elif "log" in record:
                    self.log(record["log"], record.get("level", "INFO"), record.get("timestamp"))
                else:
                    raise ValueError("unrecognized search record")
            except (TypeError, KeyError, ValueError):
                self._protocol_error = True
                self.log("Invalid worker response; unconfirmed work will be retained.", "ERROR")

    def _accept_result(self, result):
        theory_id = result["theory_id"]
        job = self._calculation_jobs[theory_id]
        remaining = result["remaining_fields"]
        if (theory_id in self._results or self._calculation_complete is not None
                or result["lagrangian_realization_id"] != job["lagrangian_realization_id"]
                or not isinstance(remaining, list) or len(set(remaining)) != len(remaining)
                or not set(remaining) <= {"superconformal_index", "coulomb_branch_index", "coulomb_branch_spectrum"}):
            raise ValueError("invalid calculation result")
        self._results[theory_id] = remaining

    def _apply_calculation_results(self):
        for key, group in list(self._groups.items()):
            retained = []
            for job in group["jobs"]:
                if job["theory_id"] in self._results:
                    job["needed_fields"] = self._results[job["theory_id"]]
                if job["needed_fields"]:
                    retained.append(job)
            if retained:
                group["jobs"] = retained
            else:
                del self._groups[key]
        self._render_groups()

    def _error(self, error):
        if self.process is None:
            return
        if not self._cancelled:
            label = "Search" if self._mode == "search" else "Index calculation"
            self.log(f"{label} process: {self.process.errorString()}", "ERROR")
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._finished(-1, QtCore.QProcess.ExitStatus.CrashExit)

    def _finished(self, exit_code, exit_status):
        if self.process is None:
            return
        self._read_output(final=True)
        self._read_output(final=True, stderr=True)
        if self._mode == "calculation":
            self._apply_calculation_results()
            if (self._protocol_error or self._calculation_complete is None
                    or exit_status != QtCore.QProcess.ExitStatus.NormalExit):
                self.log("Index calculation ended without a complete report; unconfirmed jobs were kept. "
                         "A retry will recheck their saved components.", "ERROR")
            self._calculation_jobs = self._results = {}
        else:
            success = (not self._cancelled and not self._protocol_error and exit_code == 0
                       and exit_status == QtCore.QProcess.ExitStatus.NormalExit
                       and self._complete == len(self._seen_ids))
            if success:
                self._publish()
                self.log(f"Found {self._complete} theories in {len(self._groups)} gauge groups. "
                         "Results are retained in this window for calculation.")
            elif not self._cancelled:
                self.log("Search failed or returned incomplete results; previous results were kept.", "ERROR")
        self.process.deleteLater()
        self.process = None
        self._request = b""
        self.logger.close_file()
        self.logger.set_secrets()
        self._pending = {}
        self._stdout = self._stderr = b""
        self.set_available(self._available)
        self._sync_select_all()
        self.activeChanged.emit(False)

    def _publish(self):
        self._groups = dict(sorted(self._pending.items()))
        self._database = self._source
        self._render_groups()

    def _render_groups(self):
        listing = self.window.emptyIndexGaugeGroupsList
        selected = {tuple(listing.item(i).data(QtCore.Qt.ItemDataRole.UserRole))
                    for i in range(listing.count())
                    if listing.item(i).checkState() == QtCore.Qt.CheckState.Checked}
        blocker = QtCore.QSignalBlocker(listing)
        listing.clear()
        for key, group in self._groups.items():
            count = len(group["jobs"])
            item = QtWidgets.QListWidgetItem(
                f"{group['label']} ({', '.join(key)}) — {count} {'theory' if count == 1 else 'theories'}"
            )
            item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                          | QtCore.Qt.ItemFlag.ItemIsSelectable)
            item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
            item.setCheckState(QtCore.Qt.CheckState.Checked if key in selected else QtCore.Qt.CheckState.Unchecked)
            listing.addItem(item)
        if listing.count():
            listing.setCurrentRow(0)
        del blocker

    def stop(self):
        if self.process is None or self._mode != "calculation" or self._cancelled:
            return
        self._cancelled = True
        self.log("Stop requested; waiting for active components to finish and save. "
                 "Unfinished jobs will remain selected.")
        if self._request:
            self._request += b"stop\n"
        else:
            self.process.write(b"stop\n")
        self._sync_controls()

    def cancel_for_close(self):
        if self.process is not None:
            if self._mode == "calculation":
                self.stop()
                return
            self._cancelled = True
            self.log("Closing the window; cancelling the database search.")
            # This worker is read-only. Killing it releases its DB connection and
            # cannot interrupt an import, migration or index write.
            self.process.kill()
