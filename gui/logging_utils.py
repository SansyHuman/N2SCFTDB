"""Shared log records and GUI/file output, without Qt or backend dependencies.

Workers can emit ``make_log_record(...)`` as JSON. GUI controllers use a
``GuiLogger(widget.appendPlainText)`` on the GUI thread. File output is optional.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path


def redact_message(message, secrets=()):
    """Redact complete secrets before splitting multiline messages."""
    text = str(message)
    if isinstance(secrets, str):
        secrets = (secrets,)
    for secret in sorted({value for value in secrets if value}, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def make_log_record(message, level="INFO", timestamp=None, *, secrets=()):
    """Build a JSON-compatible log record with local time and a UTC offset."""
    level = str(level).upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level = "INFO"
    try:
        moment = datetime.fromisoformat(timestamp) if timestamp else datetime.now().astimezone()
    except (TypeError, ValueError):
        moment = datetime.now().astimezone()
    return {
        "log": redact_message(message, secrets),
        "level": level,
        "timestamp": moment.astimezone().isoformat(sep=" ", timespec="milliseconds"),
    }


def format_log_message(message, level="INFO", timestamp=None, *, secrets=()):
    """Prefix every line with the same timestamp and normalized severity."""
    record = make_log_record(message, level, timestamp, secrets=secrets)
    prefix = f"[{record['timestamp']}] [{record['level']}] "
    return "\n".join(prefix + line for line in (record["log"].splitlines() or [""]))


class GuiLogger:
    """Append identical formatted messages to a widget and an optional UTF-8 file.

    ``append_text`` is normally ``QPlainTextEdit.appendPlainText``. Set secrets
    before processing worker output, then clear them after closing the file.
    File failures are reported to the widget; subsequent GUI logging continues.
    """

    def __init__(self, append_text):
        self._append_text = append_text
        self._secrets = ()
        self._stream = None
        self.path = None
        self._file_label = "log"

    def set_secrets(self, *secrets):
        self._secrets = tuple(value for value in secrets if value)

    def log(self, message, level="INFO", timestamp=None):
        rendered = format_log_message(message, level, timestamp, secrets=self._secrets)
        self._append_text(rendered)
        if self._stream is not None:
            try:
                self._stream.write(rendered + "\n")
                self._stream.flush()
            except (OSError, UnicodeError) as exc:
                self.close_file()
                self.log(f"Cannot write {self._file_label} at {self.path}: {exc}", "ERROR")

    def start_file(self, directory, *, prefix="log", label="log"):
        """Open a fresh timestamped file, preserving existing files on collisions.

        ``prefix`` is a filename stem (for example ``log_anomalies`` or
        ``log_indices``); ``label`` is used in the status/error messages.
        Returns the new path, or None if the file could not be created.
        """
        if not prefix or Path(prefix).name != prefix or prefix in {".", ".."}:
            raise ValueError("log prefix must be a filename stem")
        self.close_file()
        self._file_label = label
        self.path = Path(directory)
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now()
            while True:
                path = Path(directory) / f"{prefix}_{timestamp:%Y%m%d_%H%M%S_%f}.log"
                self.path = path
                try:
                    self._stream = path.open("x", encoding="utf-8", newline="\n")
                    break
                except FileExistsError:
                    timestamp += timedelta(microseconds=1)
        except OSError as exc:
            self.log(f"Cannot create {label} at {self.path}: {exc}", "ERROR")
            return None
        self.log(f"Saving {label} to {self.path}")
        return self.path if self._stream is not None else None

    def close_file(self):
        # Detach before reporting an error so a failed close cannot recursively
        # attempt to write to, or close, the same broken stream.
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.close()
            except (OSError, UnicodeError) as exc:
                self.log(f"Cannot close {self._file_label} at {self.path}: {exc}", "ERROR")
