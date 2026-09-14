# Tests

Keep all new test code in this directory, including GUI tests and their
fixtures/helpers. Application modules in `gui/` contain runtime code only.
The moved GUI modules use the `test_gui_` prefix, alongside the existing
GUI-worker tests.

From the project root, run the combined backend and GUI suite with Sage:

```bash
N2_TEST_MYSQL_DATABASE= QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  sage -python -B -m unittest discover -s test -p 'test_*.py' -v
```

The command above skips live MySQL integrations. To include them, set
`N2_TEST_MYSQL_DATABASE` and the other `N2_TEST_MYSQL_*` connection values to a
disposable test database, then omit the empty database assignment. Live tests
reset their configured test tables. `all_test.sh` also discovers this directory
and retains its existing local connection configuration.

To run only the window, index-tab and shared-logging tests:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 sage -python -B \
  -m unittest test.test_gui_n2_db test.test_gui_index_tab \
  test.test_gui_logging_utils -v
```

These tests use temporary settings and fixture credentials. The opt-in real
Secret Service check remains separate from unittest discovery:

```bash
sage -python -B test/check_secret_service.py
```

It creates a private D-Bus session and a disposable keyring. It requires
`dbus-run-session`, `gnome-keyring-daemon`, and the GUI's Python dependencies.
