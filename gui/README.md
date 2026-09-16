# N2SCFTDB GUI

Activate your Sage environment or add the directory containing `sage` to
`PATH`. From the project root, launch the GUI with:

```bash
sage -python -B gui/n2_db.py
```

If the GUI dependencies are not installed in Sage's Python environment, install
them first:

```bash
sage -python -m pip install -r gui/requirements.txt
```

Alternatively, install the GUI dependencies in a Python environment:

```bash
python -m pip install -r gui/requirements.txt
python gui/n2_db.py
```

The shell itself does not require Sage, FORM, LiE or a
MySQL connection. It also works with `python -m gui.n2_db` from the project root.

Open `n2_db.ui` and `settings.ui` in Qt Creator / Qt Widgets Designer to edit
their layouts. The main window contains the `anomaly` and `index`
tabs. Choose **Settings → Preferences…** (Ctrl+,) for settings; **File → Quit**
(Ctrl+Q) closes the program. Python loads both UI files directly, so no code
generation step is needed.

In **Settings → Preferences…**, the MySQL section has **Delete all database
contents…**. It opens a warning showing the database, server/socket and account
currently displayed in Settings, including unsaved edits. Enter that account's
current password in the separate masked field and press **Delete**. The password
is authenticated by a fresh MySQL connection; it is never taken from the saved
password, saved back to preferences, or sent through command-line arguments.
The dialog requires a nonempty password and remains responsive while deletion runs.

Deletion removes every stored theory and its related properties, realizations,
gauge/matter/flavor data, indices and spectra in one transaction. The tables,
schema metadata, database account and SQLite cache files remain. A wrong password
performs no deletion; database errors roll back the transaction. If a connection
or worker failure leaves the outcome unconfirmed, inspect the database before
retrying. After success the index tab's retained search results are cleared,
even if Settings is subsequently cancelled. Settings/deletion are unavailable
during an active build/search/calculation; an active deletion must finish before
its dialog can close. This operation cannot be undone after it commits.

In the **index** tab, **Search theories with empty indices** uses the saved
MySQL settings and the existing `iter_lagrangian_index_jobs` iterator. A theory
is listed if its full index, Coulomb index **or** Coulomb spectrum is missing
(SQL NULL or JSON null). A complete result of lower or unknown precision is
not selected for an upgrade by this search. One stored Lagrangian realization
is retrieved per shared theory, then grouped by its gauge factors, with a
theory count beside each checkbox. Product-factor order follows the database.
Groups initially start unchecked. **Select all** checks or clears every group;
checking individual groups also updates **Select all**.

Search runs in a separate Sage process so the window stays responsive. It opens
an existing initialized database without schema migration and performs no index
calculation or database writes. It uses the saved host, port, user, password and
connection timeout, plus `N2_DB_UNIX_SOCKET` when set. Errors and search progress
appear in the right-hand log with the anomaly log's timestamp/level format and
password redaction. Build and Preferences are unavailable while searching;
search is unavailable during an anomaly build. Worker timestamps are retained.
Closing cancels the read-only search process and waits asynchronously for it to exit.

A successful search retains the complete job inputs, theory and realization
IDs, missing fields and unknown-precision metadata in memory for the lifetime
of the window. `window.index_tab.retrieved_jobs` returns all retained jobs and
`window.index_tab.selected_jobs` returns those in checked groups, without another
database search. `window.index_tab.database` identifies their source without a
password. These accessors return copies. A successful refresh replaces the
snapshot and preserves checks for groups still present; an empty successful
search clears the list. Failed/incomplete searches keep the previous snapshot.
Changing the source database in Preferences clears it; changing calculation
cutoffs does not. Results are not saved across application restarts.

**Calculate index** uses the retained jobs for checked groups. A separate Sage
coordinator distributes all selected theories across spawned worker processes,
using the saved CPU limit (`-1` means all logical cores), capped at the selected
theory count. Work is assigned dynamically, with at most twice that many jobs
queued/running. Each worker owns its MySQL connection and runs its full-index
cache calculations with one process, avoiding nested worker pools. Both saved
cache filenames, executables, timeout and inclusive index cutoffs are honored.
Calculation uses the initialized database from the search, without migration.

Each job rechecks its missing components by theory/realization ID, without
repeating the database-wide search. It computes the full index, Coulomb index
and complete Coulomb spectrum as needed. Existing components are preserved,
including legacy results with unknown cutoffs and results filled by another
client since the search. Each successful component commits independently.
Finished theories disappear from the retained list when the run ends; failed,
stopped and unconfirmed jobs remain, with their group selections, for retry.
Failures in one calculation component do not discard other completed components.
Database connection/rollback failures stop that job and close its connection.

During calculation the button becomes **Stop**. Stop cancels queued work and
lets active components finish and save before workers exit. Closing the window
requests the same cooperative stop. Settings, search and anomaly builds are
disabled until shutdown completes. Progress and per-component errors appear
as they arrive; worker logs use a manager queue, avoiding multiprocessing feeder
threads waiting on undrained pipes during shutdown.

Index writes lock the existing parent theory before its property/realization
rows. Existing-theory anomaly attachments use the same parent-first order;
new theories retain the direct-property-insert fix. Calculations hold no MySQL
locks. Deadlocks and lock timeouts (1213/1205) roll back the entire short write
transaction and retry at most twice (50/100 ms backoff), rechecking stored data
without repeating calculations. Lost connections and uncertain commits are
reported rather than replayed. Other clients can still cause contention; see
[MySQL's deadlock guidance](https://dev.mysql.com/doc/refman/8.0/en/innodb-deadlocks-handling.html).

Every search and calculation attempt creates
`logs/log_index_YYYYMMDD_HHMMSS_ffffff.log` under the project root, with the path
shown in the GUI. The file contains the same timestamped, password-redacted
messages as the GUI log, flushed as they arrive, including errors before
the worker starts. Each attempt gets a new file; it closes after success,
failure or cancellation. File errors appear in the GUI and processing continues
with on-screen logging.

In the **anomaly** tab, enter gauge groups in the left text field, one theory
per line. Separate product-group factors with commas, for example `A1, C2`.
**Load theories...** selects one or more UTF-8 text files (an optional BOM and
Windows line endings are accepted) and appends them in selection order. Each
file starts on a new line, and existing text stays intact. The entire load is
one undoable edit; the usual editing, paste, Undo and Redo shortcuts work.
Cancel and empty files leave the input unchanged. If any selected file cannot
be read or decoded, an error identifies the file and the whole load leaves the
input unchanged. Loaded text remains editable.

**Build** uses the saved MySQL connection settings and processes each nonempty
line. It accepts the backend's Cartan types (`A1`, `C2`, etc.); blank factors
and unsupported groups are reported with their line numbers. A single factor
uses `enumerate_simple_theory_candidates`; comma-separated factors use
`enumerate_product_theory_candidates`. Each candidate is checked with
`calculate_n2_theory_properties`, and valid SCFTs are inserted through
`store_lagrangian_theory`. The coordinator initializes/migrates the database
before dispatching candidates. Imports store basic properties; index and
Coulomb-spectrum calculations remain separate.

For each gauge group, **CPU cores** controls the worker count, capped at the
number of candidates. Enumeration itself runs once in the coordinator, then
spawned processes check candidates and insert valid theories in parallel.
Candidates are dispatched individually as workers become available, with at
most twice the worker count queued or running. This balances variable checking
costs without copying the full candidate list into every worker. `1` uses the
serial path. A group's workers finish before the next group starts.

Each worker owns its Sage state and one MySQL connection, reused for its tasks
and closed when the worker exits. No connection or cursor is shared between
threads or processes. Worker connections and imports use
`initialize_schema=False` after the coordinator completes schema setup. Each
theory is inserted in its own InnoDB transaction. Fresh theories insert their
properties directly: locking a properties row that does not yet exist would
take a gap lock and can deadlock concurrent inserts of different theories.
Attaching to an existing theory still locks its shared properties for comparison
and merging. Concurrent equivalent imports use the existing unique keys:
the losing transaction rolls back and reports
the committed theory as already present. Deadlocks and lock timeouts retry the
entire rolled-back transaction at most twice. Other database failures are logged;
an uncertain commit is not retried automatically. Counters, the representation
union, and log output are updated only by the coordinator from worker results.

The log reports the current group, candidate count, valid/invalid theories,
new additions and existing records, with group and overall totals. Invalid
theories include the rejection reason. Unexpected checking errors and database
errors have separate counters, so failed calculations are not labelled invalid
theories. A failed group or candidate is logged and later work continues when
possible. Repeated input lines are processed again and normally count as existing
records after their first insertion.

Each **Build** attempt automatically creates a UTF-8 text log under the project
root's `logs/` directory, named `log_anomalies_YYYYMMDD_HHMMSS_ffffff.log` using
local time (the last six digits are microseconds). The log window shows the
file's full path. Progress, error reasons, counts and Stop messages are written
and flushed as they arrive, including errors that prevent a build from starting.
Each attempt gets a separate file; existing logs are never overwritten. The
file closes when the attempt finishes, fails or stops. Passwords are redacted
before messages reach either the window or the file. File creation/write errors
are shown in the window, and the build continues with on-screen logging.
Generated logs are ignored by Git.

Both the file and GUI use `[YYYY-MM-DD HH:MM:SS.mmm±HH:MM] [LEVEL] message`,
with local time, milliseconds and a UTC offset. Worker messages retain their
emission time; GUI messages use the time they are logged. Every line of a
multi-line message carries the same prefix. Progress uses `INFO`, rejected
theories and summaries with errors use `WARNING`, and failures use `ERROR`.
Unstructured stdout is logged as `INFO` and stderr diagnostics as `WARNING`;
worker crashes and nonzero exits are reported separately as `ERROR`.

When **Build character decomposition cache** is selected, the worker collects
the distinct `(Cartan type, Dynkin labels)` pairs across all valid theories,
including existing records and valid candidates whose database insertion failed.
It includes vector adjoints, factor singlets and full-hyper conjugates. After
processing the theory list, it calls `build_decomposition_cache` for each pair
through `index/full_max_order // 2`, using the saved character-cache filename,
LiE executable, LiE/FORM timeout and CPU core count. A zero bound skips
prebuilding. Progress reports computed/reused products at each Adams order;
existing cache rows are reused. The FORM-cache path, FORM executable and Coulomb
cutoff apply to index calculations, not this action.

Build runs in a separate process, using `sage -python` beside the GUI's Python
interpreter or the `sage` executable on PATH. This requires the project's Sage,
OR-Tools and MySQL dependencies; cache generation also needs LiE. The window
stays responsive. Settings and inputs are fixed for the duration of a build.
**Stop** stops candidate submission, cancels queued tasks where possible, and
signals workers to stop before their next check or insert. Active calls finish
and return their results before the final totals are logged, preserving counts
for committed inserts. An active enumeration must finish first; character-cache
generation stops after the current cache order is committed. Candidate workers
exit before character-cache workers start, so their process counts do not multiply.
Closing the window requests the same stop and closes once the worker exits.
Previously committed database/cache work is retained. Credentials are passed
through the worker's stdin pipe and are redacted from displayed error messages.

**OK** saves settings immediately. **Cancel**, Escape and the dialog close
button discard edits. Saved values load on later launches and take precedence
over default values and environment variables. On Linux the settings file is
`~/.config/SuperconformalIndex/n2_db.ini`, or
`$XDG_CONFIG_HOME/SuperconformalIndex/n2_db.ini` when that variable is set.
Other platforms use Qt's generic per-user configuration directory. Persistence
uses [QSettings](https://doc.qt.io/qt-6/qsettings.html) in INI format, with atomic
file replacement and owner-only permissions (0600 on POSIX).

The configuration directory and keyring service retain their original identifiers
for compatibility with saved preferences and passwords. These are storage IDs,
not the current project name. Cache paths inside the previous sibling checkout
are rebased to the current `N2SCFTDB` root when loaded and persisted on the next
settings save. Custom cache paths outside the previous checkout stay as entered.

Passwords are stored in the native system credential vault via
[Python keyring](https://keyring.readthedocs.io/en/latest/): Linux Secret Service
(for example, GNOME Keyring), macOS Keychain, or Windows Credential Manager.
The INI contains only an opaque `mysql/password_id` reference. No encryption
key or password is stored beside it. Configurable plaintext keyring backends
are bypassed. GNOME Keyring encrypts its password collection using its master
password; use a password-protected keyring. See the
[GNOME security description](https://wiki.gnome.org/Projects/GnomeKeyring/SecurityFAQ).
Encryption protects stored credentials; an unlocked desktop session can access
them through the vault, and the app necessarily holds the password in memory.

The desktop may ask you to unlock your keyring. If secure storage is unavailable,
the GUI reports an error and does not fall back to writing a plaintext password.
An empty saved password requires no vault entry and overrides `N2_DB_PASSWORD`.
Changing or clearing a password removes the previously referenced entry after
the new settings are saved. If cleanup cannot reach the vault, an unused encrypted
entry can remain under the service `SuperconformalIndex.N2Database.MySQL`.

Existing `mysql/password` plaintext settings migrate automatically at startup
or when settings are read: the app saves and reads back the password in the
vault, then atomically replaces the INI without its plaintext key. Failed
migration preserves the original file for retry and displays an error. This
does not erase old filesystem snapshots or backups. Copying only the INI to
another computer does not copy its password; restore the keyring entry or remove
`mysql/password_id` from the INI and enter the password again.

Defaults mirror `index/char_decomposition_cache.py`,
`index/form_expansion_cache.py`, `index/n2_theory_index.py`,
`common/n2_theory_db.py`, and `common/n2_theory_properties.py`:

| Setting | First-run default |
| --- | --- |
| Full superconformal index maximum order | `18` (power of `t`, `INDEX_MAX_ORDER`) |
| Coulomb index maximum dimension | `90` (`C_INDEX_MAX_ORDER`) |
| Character decomposition cache | Project-root `char_decomposition_cache.db` |
| FORM expansion cache | Project-root `form_expansion_cache.db` |
| MySQL database | Empty; the backend requires an explicitly supplied name |
| MySQL host | `N2_DB_HOST`, otherwise `127.0.0.1` |
| MySQL port | `3306` |
| MySQL user | `N2_DB_USER`, otherwise `root` |
| MySQL password | `N2_DB_PASSWORD`, otherwise empty |
| MySQL connection timeout | `10` seconds |
| LiE executable | `lie` (resolved through PATH) |
| FORM executable | `form` (resolved through PATH) |
| LiE / FORM timeout | `600` seconds per invocation |
| CPU cores | `-1` (all system cores) |

**CPU cores** in **Computation tools** is saved as `tools/processes`. The default
`-1` uses the system's logical CPU count (`os.cpu_count()`, falling back to one
if unavailable), resolved when each build starts. A positive integer limits the
number of worker processes; `1` runs serially and `0` is not allowed. This setting
controls candidate checking, database insertion and character-cache generation
in the anomaly build, and parallel theories in index calculation. Theory enumeration remains serial. Older settings files
default to `-1` until saved.

The **Index truncation** section saves both inclusive cutoffs. Full-index order
is a nonnegative integer; the Coulomb cutoff accepts nonnegative integers or
exact fractions such as `6/5`. Coulomb dimensions are saved as exact strings
(fractions are reduced), without conversion to floating point. Older settings
files use the project defaults for these new fields until saved.
`N2DatabaseWindow.settings` exposes them as `index/full_max_order` and
`index/coulomb_max_dimension`, suitable for the backend's `order` and
`max_dimension` arguments respectively.

Cache paths can select existing files or future files. Relative cache paths
are resolved against the project root when saved. Both cache locations are
explicit settings; changing one does not silently move the other. Executables
accept a command on PATH or an absolute path. Save does not open cache databases,
connect to MySQL, or execute computation tools. Build reads a snapshot of
`N2DatabaseWindow.settings` when it starts.

## Controllers and shared logging

`N2DatabaseWindow` loads the forms, opens Preferences and coordinates tab
availability and window shutdown. Anomaly actions live in
`gui/anomaly_tab.py::AnomalyTabController`, exposed as `window.anomaly_tab`.
It owns `load_theories()`, `build_theories()`, cooperative `stop()`, the build
process and its log lifecycle. Index actions remain in
`gui/index_tab.py::IndexTabController`, exposed as `window.index_tab`.
Both expose `process` and emit `activeChanged`; the window prevents overlapping
operations through each controller's `set_available()` method. Neither
controller accesses the other controller's process internals.

`gui/logging_utils.py` provides the common logging API without importing Qt or
Sage. `make_log_record(message, level="INFO", timestamp=None, secrets=())`
creates a JSON-compatible worker record; `format_log_message(...)` formats
every line with its timestamp and level. Timestamps are ISO strings, rendered
in local time with milliseconds and UTC offset. Invalid timestamps fall back
to the current time, and unrecognized levels become INFO. Both workers use
these records, and both controllers use `GuiLogger` for GUI output.

Each controller exposes its own `logger`. The logger redacts configured
secrets and can write identical, immediately flushed UTF-8 messages to an
optional file. `start_file()` creates an exclusive timestamped filename and
preserves existing files; file failures are reported in the widget while GUI
logging continues. Anomaly builds retain the existing `log_anomalies_...log`
files; index searches and calculations use `log_index_...log`. Both use the
same logger and file API:

```python
logger = window.index_tab.logger
logger.set_secrets(password)
logger.start_file(PROJECT_ROOT / "logs", prefix="log_index", label="index log")
try:
    logger.log("Starting index calculation", level="INFO")
    # Route worker messages through logger.log(message, level, timestamp).
finally:
    logger.close_file()
    logger.set_secrets()
```

Call `GuiLogger` on the GUI thread, for example from a QProcess output handler.
Worker processes should print JSON from `make_log_record()` instead of touching
widgets. File closure precedes clearing secrets so close errors are redacted.

## Validation

All GUI test code lives in `test/`; add new tests there as well. See
`test/README.md` for the combined suite. To run just the GUI and logging
regressions without opening desktop windows:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  sage -python -B \
  -m unittest test.test_gui_n2_db test.test_gui_index_tab \
  test.test_gui_logging_utils -v
```

These tests use an in-memory credential test double, never your desktop vault.
For an additional real Linux Secret Service check, run:

```bash
sage -python -B test/check_secret_service.py
```

This opt-in check requires `dbus-run-session` and `gnome-keyring-daemon`. It
creates a private D-Bus session and temporary XDG directories, initializes a
password-protected disposable keyring, and checks migration, a fresh GUI process,
the encrypted keyring file and password removal. It never uses personal
credentials or the normal desktop keyring. Sandboxes that prohibit local sockets
cannot run this integration check.
