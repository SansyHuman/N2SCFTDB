# Tests

Keep all new test code in this directory, including GUI tests and their
fixtures/helpers. Application modules in `gui/` contain runtime code only.
The moved GUI modules use the `test_gui_` prefix, alongside the existing
GUI-worker tests.

Index tests pass character cache filenames with `char_cache_database_path`;
CLI tests use `--char-cache-database` for both direct-file and module execution.
Property/worker tests override `CHAR_CACHE_DATABASE_PATH` with a temporary file.
The standalone character-cache class and builder retain their own path API.

`test_gui_database_clear.py` checks the Settings deletion warning, target display,
fresh masked password, cancellation/retry, secret redaction, asynchronous process
lifetime and invalidation of cached index jobs. `test_database_clear.py` checks
authentication, schema validation and rollback. Its opt-in MySQL tests delete only
the dedicated `N2_TEST_MYSQL_DATABASE` fixtures, test the real stdin worker protocol,
verify all theory-data tables are empty after success, and re-import into the
preserved schema. They require only database-scoped test privileges.

`test_form_degree_bound.py` compares the degree-bounded FORM exponential with
the former unrestricted multiplication before singlet projection. Cases include
empty/free sectors, pure vectors, simple and multi-factor matter, minimal/odd
cutoffs and the default order 18. `benchmark_product_form.py --variant baseline`
retains the former multiplication; `--variant degree_bounded` profiles production.

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

`test_gui_theory_download.py` exercises CSV fidelity, one row per realization,
missing joins, fixed theory selection, bounded real spawned readers, deterministic
serial/parallel output, theory-count progress, cancellation and atomic publication
using a disposable SQLite fixture. `test_gui_search_tab.py` uses real Qt fixture
processes to verify save-dialog cancellation, selected fields, progress percentages,
error handling, cooperative shutdown and retention of searched IDs. These tests
never access the configured MySQL database.

`test_n2_theory_db.py` checks fresh schema-1 initialization, acceptance of the
current version and rejection of unsupported versions. Migration and backfill
tests have been removed with their implementation.

`test_n2_theory_db_sectors.py` checks input factor IDs and shared-property
comparisons. Its opt-in MySQL cases verify connected, disconnected and free-sector
JSON storage, and preservation of the first realization's factor names when
other realizations or duplicate aliases are imported. Use only a disposable
`N2_TEST_MYSQL_DATABASE` for these cases.

To run only the window, index-tab and shared-logging tests:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 sage -python -B \
  -m unittest test.test_gui_n2_db test.test_gui_index_tab \
  test.test_gui_logging_utils -v
```

For the index calculation workers and database write regressions:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 sage -python -B \
  -m unittest test.test_gui_index_calculator test.test_n2_theory_db_indices -v
```

With a disposable MySQL database configured, these cover real Sage/FORM/LiE
calculations, simple/product groups, exact cutoffs, custom shared cache files,
stale selections, partial retries, cooperative stop and worker connection cleanup.
They also exercise the actual GUI-to-Sage process protocol, a 256-theory run
with eight index workers, and parent-row contention. Concurrency tests record
rollbacks on their own connections, including lock failures that the backend
successfully retries, and require none in the no-deadlock scenarios. They do not
query the privileged server-wide `information_schema.INNODB_METRICS` table.
Connection cleanup and contention checks use `PROCESSLIST` only for connections
owned by the configured test user, which does not require the global `PROCESS`
privilege. Database-scoped test permissions are sufficient. The normal suite
also checks bounded scheduling and a real abrupt worker exit without MySQL.

`test_disconnected_index.py` checks NetworkX sector partitioning, transitive
and trifundamental connections, zero multiplicities, free singlet hypers,
exact truncated products, repeated sectors and optional MySQL index reuse.
It compares split calculations with the original unsplit FORM projection.
The live database cases cover canonical representation matching, missing/null
indices, unknown/lower/equal/higher cutoffs, read-only connection ownership,
and the worker's reuse of connected product sectors alongside simple sectors.

These tests use temporary settings and fixture credentials. The opt-in real
Secret Service check remains separate from unittest discovery:

```bash
sage -python -B test/check_secret_service.py
```

It creates a private D-Bus session and a disposable keyring. It requires
`dbus-run-session`, `gnome-keyring-daemon`, and the GUI's Python dependencies.
