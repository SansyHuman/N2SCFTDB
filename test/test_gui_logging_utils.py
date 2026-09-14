"""Reusable log formatting, redaction and optional-file failure behavior."""

from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui.logging_utils import GuiLogger, format_log_message, make_log_record


class LoggingTests(unittest.TestCase):
    def test_worker_records_preserve_time_and_prefix_each_redacted_line(self):
        timestamp = "2026-09-14 10:20:30.456-03:30"
        record = make_log_record("진행: long secret\nshort", "warning", timestamp,
                                 secrets=("short", "secret", "long secret"))
        expected_stamp = datetime.fromisoformat(timestamp).astimezone().isoformat(
            sep=" ", timespec="milliseconds",
        )
        self.assertEqual(record, {
            "log": "진행: [redacted]\n[redacted]", "level": "WARNING", "timestamp": expected_stamp,
        })
        rendered = format_log_message(record["log"], record["level"], record["timestamp"])
        self.assertEqual(rendered, f"[{expected_stamp}] [WARNING] 진행: [redacted]\n"
                                  f"[{expected_stamp}] [WARNING] [redacted]")
        self.assertEqual(make_log_record("a secret", secrets="secret")["log"], "a [redacted]")

    def test_invalid_metadata_falls_back_to_valid_local_time_and_info(self):
        for timestamp in ("bad timestamp", 123):
            with self.subTest(timestamp=timestamp):
                before = datetime.now().astimezone()
                record = make_log_record(123, "invalid\nlevel", timestamp)
                after = datetime.now().astimezone()
                parsed = datetime.fromisoformat(record["timestamp"])
                self.assertIsNotNone(parsed.utcoffset())
                self.assertEqual(record["level"], "INFO")
                self.assertEqual(record["log"], "123")
                self.assertLessEqual(before.replace(microsecond=before.microsecond // 1000 * 1000), parsed)
                self.assertLessEqual(parsed, after)
        self.assertTrue(format_log_message("").endswith("[INFO] "))

    def test_independent_loggers_can_write_files_and_clear_secrets(self):
        left, right = [], []
        anomaly, index = GuiLogger(left.append), GuiLogger(right.append)
        with tempfile.TemporaryDirectory(prefix="n2-shared-logs-") as directory:
            self.addCleanup(anomaly.close_file)
            self.addCleanup(index.close_file)
            anomaly.set_secrets("anomaly password")
            index.set_secrets("index password")
            anomaly_path = anomaly.start_file(directory, prefix="log_anomalies", label="anomaly log")
            index_path = index.start_file(directory, prefix="log_indices", label="index log")
            anomaly.log("anomaly password / index password")
            index.log("anomaly password / index password")
            self.assertIn("[redacted] / index password", left[-1])
            self.assertIn("anomaly password / [redacted]", right[-1])
            self.assertEqual(anomaly_path.read_text(), "\n".join(left) + "\n")
            self.assertEqual(index_path.read_text(), "\n".join(right) + "\n")
            anomaly.close_file()
            index.close_file()
            index.set_secrets()
            index.log("index password")
            self.assertTrue(right[-1].endswith("index password"))
            self.assertNotIn(right[-1], index_path.read_text())

    def test_failed_write_and_close_do_not_recurse_or_disable_gui_output(self):
        messages = []
        logger = GuiLogger(messages.append)
        logger.set_secrets("secret")
        stream = MagicMock()
        stream.write.side_effect = OSError("secret disk full")
        stream.close.side_effect = OSError("secret close error")
        logger._stream = stream
        logger.log("progress")
        self.assertIsNone(logger._stream)
        stream.close.assert_called_once()
        self.assertEqual(len(messages), 3)
        self.assertIn("Cannot close log", messages[1])
        self.assertIn("Cannot write log", messages[2])
        self.assertNotIn("secret", "\n".join(messages))
        logger.log("still working")
        self.assertTrue(messages[-1].endswith("still working"))
        stream.write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
