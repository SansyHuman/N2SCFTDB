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
their layouts. The main window contains the `anomaly` tab and an empty `index`
tab. Choose **Settings → Preferences…** (Ctrl+,) for settings; **File → Quit**
(Ctrl+Q) closes the program. Python loads both UI files directly, so no code
generation step is needed.

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
theory is inserted in its own InnoDB transaction. Concurrent equivalent imports
use the existing unique keys: the losing transaction rolls back and reports
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
in the anomaly build. Theory enumeration remains serial. Older settings files
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

To run the GUI regression checks without opening desktop windows:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 \
  sage -python -B \
  -m unittest discover -s gui -p 'test_*.py' -v
```

These tests use an in-memory credential test double, never your desktop vault.
For an additional real Linux Secret Service check, run:

```bash
sage -python -B gui/check_secret_service.py
```

This opt-in check requires `dbus-run-session` and `gnome-keyring-daemon`. It
creates a private D-Bus session and temporary XDG directories, initializes a
password-protected disposable keyring, and checks migration, a fresh GUI process,
the encrypted keyring file and password removal. It never uses personal
credentials or the normal desktop keyring. Sandboxes that prohibit local sockets
cannot run this integration check.
