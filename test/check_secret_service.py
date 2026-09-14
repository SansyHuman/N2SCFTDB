"""Opt-in Linux integration check using a disposable, password-protected keyring.

Run with the GUI Python interpreter. The outer process creates a private D-Bus
session and temporary XDG directories; the user's actual keyring is never used.
"""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = " n2 integration dummy = # 비밀번호 "


def inside_session(directory):
    sys.path.insert(0, str(ROOT))
    from PyQt6 import QtCore
    import secretstorage
    from jeepney import DBusAddress, new_method_call
    from gui.n2_db import SettingsStore

    daemon = subprocess.Popen(
        ["/usr/bin/gnome-keyring-daemon", "--foreground", "--unlock",
         "--components=secrets", "--control-directory", str(directory / "control")],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        daemon.stdin.write(b"disposable-integration-master-password")
        daemon.stdin.close()
        deadline = time.monotonic() + 10
        while True:
            try:
                connection = secretstorage.dbus_init()
                try:
                    # Query D-Bus itself first: looking up a collection too early
                    # can auto-activate a second daemon and race our test daemon.
                    request = new_method_call(
                        DBusAddress("/org/freedesktop/DBus", bus_name="org.freedesktop.DBus",
                                    interface="org.freedesktop.DBus"),
                        "NameHasOwner", "s", ("org.freedesktop.secrets",),
                    )
                    owned = connection.send_and_get_reply(request).body[0]
                    ready = owned and not secretstorage.get_default_collection(connection).is_locked()
                finally:
                    connection.close()
                if ready:
                    break
            except secretstorage.exceptions.SecretStorageException:
                pass
            if daemon.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Disposable GNOME Keyring did not start")
            time.sleep(0.1)

        store = SettingsStore(directory / "n2_db.ini")
        legacy = QtCore.QSettings(str(store.path), QtCore.QSettings.Format.IniFormat)
        legacy.setValue("mysql/password", TEST_PASSWORD)
        legacy.sync()
        store.migrate_legacy_password()
        preferences = store._read_preferences()
        assert not preferences.contains("mysql/password")
        credential_id = preferences.value("mysql/password_id", type=str)
        assert credential_id
        assert store.load()["mysql/password"] == TEST_PASSWORD

        script = """
import sys
sys.path.insert(0, sys.argv[1])
from PyQt6 import QtWidgets
from gui.n2_db import N2DatabaseWindow, SettingsDialog, SettingsStore
app = QtWidgets.QApplication([])
store = SettingsStore(sys.argv[2])
window = N2DatabaseWindow(store)
dialog = SettingsDialog(store, window)
expected = sys.stdin.read()
assert window.settings['mysql/password'] == expected
assert dialog.passwordEdit.text() == expected
dialog.close()
window.close()
"""
        subprocess.run(
            [sys.executable, "-B", "-c", script, str(ROOT), str(store.path)],
            input=TEST_PASSWORD, text=True, check=True, timeout=20,
        )
        keyring_files = list((directory / "data" / "keyrings").glob("*.keyring"))
        assert keyring_files, "Expected a persistent keyring file"
        for path in keyring_files:
            contents = path.read_bytes()
            assert contents.startswith(b"GnomeKeyring\n\r\x00\n"), "Expected GNOME binary keyring format"
            assert TEST_PASSWORD.encode() not in contents
        assert TEST_PASSWORD.encode() not in store.path.read_bytes()

        values = store.load()
        values["mysql/password"] = ""
        store.save(values)
        assert store.load()["mysql/password"] == ""
        assert store.vault._get_backend().get_password(store.vault.SERVICE, credential_id) is None
        print("PASS: real Secret Service migration, fresh GUI process reload, encrypted keyring file, and credential removal")
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=5)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--inside-private-session":
        inside_session(Path(sys.argv[2]))
        return
    with tempfile.TemporaryDirectory(prefix="n2-private-keyring-") as temporary:
        directory = Path(temporary)
        environment = dict(os.environ)
        for name in (
            "DBUS_SESSION_BUS_ADDRESS", "GNOME_KEYRING_CONTROL", "GNOME_KEYRING_PID",
            "DISPLAY", "WAYLAND_DISPLAY",
        ):
            environment.pop(name, None)
        for variable, child in (
            ("XDG_DATA_HOME", "data"), ("XDG_CONFIG_HOME", "config"),
            ("XDG_RUNTIME_DIR", "runtime"),
        ):
            path = directory / child
            path.mkdir(mode=0o700)
            environment[variable] = str(path)
        (directory / "control").mkdir(mode=0o700)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        session = subprocess.Popen(
            ["/usr/bin/dbus-run-session", "--", sys.executable, "-B", str(Path(__file__).resolve()),
             "--inside-private-session", str(directory)],
            env=environment, start_new_session=True,
        )
        try:
            returncode = session.wait(timeout=45)
            if returncode:
                raise subprocess.CalledProcessError(returncode, session.args)
        finally:
            # Stop the whole private session, even if a service or prompt stalls.
            try:
                os.killpg(session.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                session.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(session.pid, signal.SIGKILL)
                session.wait()


if __name__ == "__main__":
    main()
