# N2SCFTDB Project Handoff

Last updated: **2026-09-23 (Asia/Seoul)**.

## Connected product candidates before anomaly checks (23 September 2026)

`enumerate_product_theory_candidates(..., only_one_sector=True)` now performs
a lightweight connectivity filter by default. Each matter type's charged
factor positions are precomputed from its nonzero Dynkin labels. For each
beta-system solution, union-find joins the factors sharing present matter;
zero multiplicities add no edges and isolated factors remain separate.
Disconnected solutions are discarded before candidate dictionaries are built.
The filter invokes no anomaly/property calculation or full sector partition,
and the enumerator no longer imports the index module for connectivity.

The anomaly group workers inherit this default, so only connected candidates
reach property checks, database insertion and character-cache collection.
The beta-equation solver still finds all solutions; this optimization reduces
work after solving. Simple-group enumeration is unchanged. Direct callers can
set `only_one_sector=False` to retain disconnected products, with no connectivity
check on that path. Existing database rows are not removed.

Verified examples: `A1,A1` retains four of eight candidates, `A1,A1,A1` retains
18 of 50, `A1,A2` retains two of eight, and `A2,A2` retains five of 14.
The `A1` plus `A1,A1` build now checks six candidates and stores five distinct
theories (one existing duplicate); a repeat reports six existing candidates.
Earlier build-count examples below describe the previous unfiltered default.

Focused validation passed 35 tests covering connectivity, agreement with
the sector splitter, bypass of anomaly/property work during filtering,
connected-only worker inputs, and preservation of unfiltered enumeration.

Final validation: **472 tests passed, zero failures/errors/skips, in 86.254 s**,
including the full offscreen suite and live MySQL integrations on a fresh
isolated temporary server. Live builds confirmed six checked candidates,
five inserted theories and one existing duplicate, followed by six existing
candidates on repeat. The configured user database was not accessed.

## Anomaly builds distribute complete gauge groups (22 September 2026)

`gui.theory_builder` now calls `gui.group_workers.process_groups` once per
build. One persistent spawned pool receives input gauge-group entries, with
at most twice the worker count outstanding. Each task enumerates its own
simple/product candidates, checks them sequentially, and imports valid SCFTs.
Candidate lists stay inside their owner process. Workers take subsequent
groups as they become available and reuse their private MySQL connections.
The configured CPU count is capped at the number of input lines; one group
uses one worker. A CPU setting of one runs serially in the coordinator.

The coordinator initializes the schema before starting workers and consumes
streamed per-group events for progress, counts and the representation union.
A synchronous progress queue preserves sent results when a worker crashes;
unknown database outcomes are reported without replaying the group. Stop
cancels queued groups, lets active enumeration finish, stops between candidate
checks/imports and drains completed results. Character-cache preparation starts
after the group pool exits and still includes all valid representations,
including existing theories and failed imports. Repeated input lines remain
separate tasks; database canonicalization retains its existing deduplication.

The older `gui.candidate_workers.process_candidates` helper remains available
and tested, but the anomaly tab no longer calls it. Its candidate-check/import
implementation and worker connection management are shared by the new scheduler.
Earlier descriptions below of coordinator enumeration and a pool per group
are superseded by this section.

Focused validation passed **32 tests**, including live isolated MySQL checks
for serial/parallel totals and cache unions, group process ownership, connection
reuse, cancellation with committed-row accounting, and worker death. A small
benchmark on `A1`, `A2`, `A1,A1`, `A1,A2` with three workers and fresh test tables
compared the preceding implementation against the new one in separate Python
processes (two runs each, cache preparation disabled). Mean build time fell
from **3.71 s to 1.51 s**; both processed 21 valid candidates with 19 added,
two existing and zero errors. This measures that workload, not a general
speedup guarantee: an expensive single group cannot use multiple workers.

Final validation: **467 tests passed, zero failures/errors/skips, in 84.396 s**
with the complete offscreen suite and live MySQL integrations enabled on the
isolated temporary server. The configured user database was not accessed.

## Product-factor permutation identity (22 September 2026)

Database identity now ignores gauge-factor order as well as factor IDs. The
canonicalizer considers the combined orbit of product-factor permutations and
each factor's diagram automorphisms, moving each algebra together with its full
column of matter labels. Repeated identical algebras are handled by the complete
matter configuration, including quiver connectivity, rather than by sorting local
signatures. Identical complete columns are permuted only once.

The canonical payload selects the least algebra sequence ordered by family and
numeric rank, then the greatest normalized whole-matter tuple. Per-hyper
simultaneous conjugation is reapplied after reordering; multiplicities, full/half
normalization, singlets and zero-removal retain their previous meaning. Simple
theory hashes remain unchanged from the outer-automorphism rule below.

New imports of reordered theories return one theory/realization, including
concurrent imports. Legacy lookup covers both earlier ordered-factor hashes and
their diagram images, without schema changes or data migration. Existing
duplicate rows remain untouched and resolve to the earliest realization on
import. Index reuse searches all equivalent hashes with the existing cutoff
rules. The first stored input order, factor IDs, sector metadata, flavor labels,
display name and indices are preserved; this change does not reorder stored
realization rows or alter enumeration/GUI grouping.

Validation: the full offscreen suite ran **459 tests: 408 passed, 51 skipped**
in 38.154 seconds with opt-in MySQL integrations disabled. Focused verification
on the isolated MySQL 8.0.46 server covered 82 distinct tests, including three
concurrent-import races. A new fixture assertion was corrected to use dictionary
cursor keys; its four-test MySQL class then passed in 3.035 seconds. All other
focused tests passed on the first run. Cases cover every ordering of a four-node
SU(2) quiver, a six-cycle versus two triangles with the same local signatures,
combined triality/permutation/full-half equivalences, old reversed hashes and
preservation of first-realization data. The configured user database was not
accessed, and the temporary server was shut down. PDFs were not rebuilt.

**Follow-up build-count regression:** the full live-MySQL run exposed a stale
expectation of ten inserts for `A1` plus `A1,A1`. Enumeration still returns ten
candidates, but the eight labelled product candidates form six factor-swap
orbits. The correct first-run counters are eight added and two existing; a repeat
reports zero added and ten existing, with eight theory/realization rows retained.
The integration assertion and the GUI build test's in-memory storage fixture now
use these semantics. A non-MySQL regression checks the product orbit sizes
`[1, 1, 1, 1, 2, 2]`.

Final validation reran the entire discovered suite with live MySQL enabled on
the isolated temporary server, using a password-protected account restricted to
its test database: **460 tests passed, zero failures/errors/skips, in 83.900
seconds**. This includes the previously skipped parallel build/cache-union test.
No application runtime code changed for this test correction, and the configured
user database was not accessed.

## Outer-automorphism database identity (22 September 2026)

Database imports now identify complete matter configurations under every finite
Dynkin-diagram automorphism of each gauge factor. This includes D4 triality,
even-D spinor exchange, and independent conjugation of whole A/D/E6 factors in
product groups. A single transformation acts on all hypers charged under that
factor; individual irreps are not independently replaced by diagram-orbit
representatives. Thus 6v/6s/6c in D4 share one identity, while 3v+3s remains
distinct. The 29 generated D4 candidates give eight stored theory identities.

`anomalies.lie_algebra.diagram_automorphisms` caches node permutations preserving
the directed, edge-labelled finite diagram. The database canonicalizer selects
the lexicographically greatest normalized whole-matter tuple, retaining existing
per-hyper simultaneous conjugation, multiplicity aggregation and pseudoreal
full/half pairing. Product-factor permutations are now included by the update
above. Enumeration and representation/flavor calculations retain their
original representation labels and candidate sets.

All new imports in an orbit share one SHA-256 hash and the existing unique
database keys. Reimports return the original theory/realization without writes,
including concurrent imports of different orbit members. `StoredTheory` returns
the current canonical hash; for an old row this can differ from its stored hash.

Import and read-only stored-index lookups also recognize the former normalized
hash of every diagram image. This permits reuse of existing schema-1 rows without
a schema change or data migration. If old duplicate rows already exist, imports
return the earliest realization; index lookup selects the highest sufficiently
precise stored string index. Existing duplicate rows are not merged or deleted.
First-realization inputs, factor IDs, flavor metadata and index cutoffs are
preserved. The older smaller-conjugate and pre-full/half hash conventions remain
outside this compatibility lookup.

These equivalences assume the existing simply connected gauge-group model;
global quotients and line-operator data are not represented.

Validation: the full offscreen suite ran **447 tests: 401 passed, 46 skipped**
in 37.010 seconds with opt-in MySQL integrations disabled. A separate temporary
MySQL 8.0.46 server, with networking disabled and its own data directory/socket,
passed all **67 focused tests** in 13.259 seconds. Those tests include real
imports, legacy hash/index reuse, preserved original data, different mixed-matter
orbits, and simultaneous imports from separate processes. The configured user
database was not accessed. Canonical PDFs were not rebuilt for this change.

## Search order/sector filters and sector CSV fields (21 September 2026)

The search tab now replaces the nonempty-index checkbox with **Minimum
full-index order**, default `0`. Zero adds no index restriction, including for
theories without indices, cutoff metadata or property rows. Positive values
require a non-whitespace full-index JSON string and a recorded
`superconformal_index_order >= minimum_index_order`; unknown precision is
excluded. The filter uses stored cutoff metadata, never the last nonzero term.
Coulomb data does not affect this filter.

**Only theories with one disconnected sector** adds the SQL condition
`disconnected_sector_count = 1`, excluding unknown counts. All search conditions
combine, including when the order is zero. Both new controls invalidate retained
results when changed and are disabled during search, export and conflicting work.
The read-only worker protocol uses `minimum_index_order` and `only_single_sector`.

The CSV selection/SQL whitelist now has **19 fields**, adding
`disconnected_sector_count` and `disconnected_sectors_json`, both checked by
default. These shared values describe the first stored realization and repeat
unchanged for every realization row. JSON text is preserved; missing SQL values
remain blank. Changing CSV selection does not invalidate search results.

Validation: **70 tests passed in 4.682 seconds** across theory search, theory
download, search controller and main GUI tests. These cover inclusive thresholds,
zero/unknown/empty indices, combined sector filters, request validation, nested
JSON fidelity, real spawned CSV readers and actual Qt fixture subprocesses.
The updated tab was rendered and visually checked offscreen. No live MySQL
database was accessed. GUI/test READMEs were updated; canonical PDFs were not
rebuilt for this change.

## Complete session documentation refresh (19 September 2026)

The two canonical PDFs and their retained LaTeX sources now document all
implemented work from this session. The implementation reference owns
GUI/database operations; the mathematical-background guide owns index
algorithms and execution. This update changes documentation only.

| Session work | Current implementation and documentation |
| --- | --- |
| FORM/TFORM selection | Settings default to one FORM thread; larger counts run TFORM. Full/Coulomb APIs and CLIs propagate the settings. |
| CPU allocation | Index theory workers use `min(jobs, max(1, CPUs // TFORM threads))`; CSV readers use the CPU setting independently with no fixed cap. |
| Timeout benchmarks | Both 16-theory cohorts, sampling provenance, all per-case measurements and historical full-save ratios are in the PDFs; report paths and limitations are below. |
| Search GUI | Editable `gui/n2_db.ui`, `1040x960` default window, theory ID, gauge grammar, nonempty-full-index option, result/progress area and CSV field controls. |
| Search actions | `SearchTabController` plus `gui/theory_search.py`; SQL exact fraction matching, approximate decimal ranges, read-only streaming and retained theory IDs. |
| CSV download | Save dialog, selected fields, one row per realization, bounded spawned readers, theory-based progress, cancellation and atomic publication. |
| Index upgrades | Search and calculation include missing components and known lower independent cutoffs; retained jobs are rechecked before calculating. |
| Public sector API | `split_disconnected_sectors` and materialized factor-ID tuples in basic properties; first-realization sector metadata stored with imports. |
| Production baseline | Complete current schema numbered 1; migrations/backfills/CLI and their tests removed. |
| Portability discussion | macOS and LAN MySQL remain assessments, not verified deployment or configuration changes. |

The updated canonical files are
`output/pdf/n2_implementation_reference_summary.pdf` (**61 pages**) and
`index/n2_theory_index_Mathematical_Background.pdf` (**29 pages**).
The previous 16 September documentation entry below is historical.

All 90 final pages were rendered for layout review. Revised material was also
checked at reading resolution. References resolve, no overfull boxes remain,
and all 48 reference/15 guide equation blocks are unchanged. Embedded guide
pages 2–4 retain identical extracted text. `output/pdf/BUILD.md` records the
page map, retained inputs and reproducible build steps. New inputs are
`tform_session_runtime.tex` (both PDFs) and `gui_search_export_workflow.tex`
(reference only). This refresh does not rerun the application suite or TFORM
benchmarks; it preserves their original dates and conditions.

## Production schema 1 baseline (19 September 2026)

The complete current MySQL layout is now **schema 1**. Fresh databases include
all current columns, including decimal central charges, index cutoff metadata
and disconnected-sector metadata. Schema initialization creates missing tables,
records version 1 for a new database and rejects any other recorded version.
It never upgrades or relabels an existing database.

The pre-release schema migration map, version-upgrade loop, sector backfill API,
migration CLI and their tests have been removed. Normal import/index operations
and tests remain. Earlier schema numbers and migration descriptions in dated
notes below are historical and superseded by this baseline. The current PDFs
now describe schema 1 directly.
Existing user databases were not changed during this code cleanup.

Validation: the full suite ran **414 tests: 374 passed, 40 skipped**. An isolated
temporary MySQL server verified fresh schema-1 creation, all ten tables, current
columns and repeated initialization; **20 integration tests passed, 2 skipped**
(the skips require a password-protected test account). These cover sector
storage, independent index upgrades and schema validation. The temporary server
was shut down and removed after the run.

### Disconnected-sector storage

`theory_properties.disconnected_sector_count INT UNSIGNED NULL` and
`disconnected_sectors_json JSON NULL` store nested lists of **input factor IDs**,
for example `[["left", "middle"], ["right"]]` with count 2. One connected
gauged component contributes one sector. The combined free-hyper sector is
one empty list and contributes one to the count, following `split_disconnected_sectors`.

The lists describe the **first stored realization** (smallest
`lagrangian_realizations.id`) of the theory. Other realizations and duplicate
imports with renamed factor IDs preserve these lists. Sector IDs are excluded
from shared physical-property equality checks. New imports store both columns
and matching `disconnected_sector_count` and `disconnected_sectors` keys in
`properties_json` within the import transaction.

`index.n2_theory_index.split_disconnected_sectors(factors, hypermultiplets)`
is public. It returns a list of `(factor_tuple, hyper_list)` pairs, preserving
input factor order within and between components. The property calculator
materializes `disconnected_sectors` as a tuple of factor-ID tuples rather than
lazy `map` iterators. A simple input uses `gauge` as its factor ID; the optional
free sector gives `()`. JSON serialization turns these into arrays. Non-SCFT
candidates receive `None`; no full index, Coulomb index or spectrum is calculated.

## Index search and calculation upgrades (19 September 2026)

The index tab's **Search missing or lower-order indices** now passes the saved
full-index order and exact Coulomb maximum dimension to the read-only search
worker. It uses `iter_lagrangian_index_jobs(upgrade=True)`, selecting missing
indices/spectra or known cutoffs strictly below Settings. Full and Coulomb
cutoffs compare independently. Unknown-only results from the backend are excluded
from the GUI selection, since unknown precision is not evidence of a lower order.

Calculate now uses `calculate_index_job(recheck=True)` with upgrades enabled.
The new `needed_lagrangian_index_fields` lookup rechecks each retained ID against
current settings, without a database-wide rescan, before calculating missing or
lower-order components. Atomic writes still preserve equal/higher and unknown
precision, including concurrent upgrades. The missing-only backend API remains
available for other callers. A settings cutoff change keeps the retained list;
rerun Search to include all theories qualifying at the new cutoffs.

Validation: **415 tests in 32.600 seconds: 377 passed, 38 skipped**, with live
database tests disabled. Six focused calculation tests also passed after the
final stale-metadata reporting adjustment. Coverage includes forwarding current
saved cutoffs, independent full/Coulomb upgrades, exact fractional comparisons,
equal/higher/unknown precision, retained-job rechecks and concurrent higher-order
writes. The user's database was not accessed.

## CSV download of searched theories (18 September 2026)

`SearchTabController` now opens a save-file dialog and runs `gui.theory_download`
for the retained theory IDs and selected CSV fields. The user chose **one row per
Lagrangian realization**. All realizations of matched theories are exported,
ordered by theory then realization ID, with shared theory properties repeated.
Theories without realizations still get one row; absent joined data is blank.
The initial 17 UI fields map to a fixed SQL whitelist; properties_json is excluded and
the theory ID is included only once. The 21 September update above adds
`disconnected_sector_count` and `disconnected_sectors_json` to the whitelist and GUI.
CSV preserves JSON and exact decimal text,
quotes commas/newlines, and uses UTF-8 with a BOM.

No search is repeated, no schema initialization runs, and the worker only issues
SELECTs. It reads batches of 32 theories using streaming cursors and
`min(resolved CPU setting, number of batches)` spawned readers. The initial
four-process cap was removed at the user's request; CPU cores `-1` uses all
logical CPUs, subject to the available batch count.
Each reader writes a disk chunk; only counts cross process boundaries. Pending
work is bounded. Small downloads use one reader. TFORM settings do not affect
CSV export. Values are fetched at export time and batches may see concurrent
external edits; IDs remain fixed, but there is no global database snapshot.
A missing searched theory fails the export rather than silently omitting it.

Progress counts theories after their rows have been merged, not CSV rows. The
worker stages files beside the destination, flushes/syncs and atomically replaces
it only on success. The GUI reports 100% after confirmed successful worker exit.
Download becomes Cancel, and window close waits for cooperative cancellation,
reader shutdown and staging cleanup. Active reads can delay cancellation.
An existing destination stays intact on failure/cancel; retained IDs can be
downloaded again. The GUI blocks conflicting operations and CSV selection edits
while exporting, requires at least one checked field, and checks database identity
again before the save dialog. Logs use `logs/log_download_*.log` with redaction.

Validation: unittest discovery ran **410 tests in 32.689 seconds: 372 passed,
38 skipped**, with the existing live-database suite disabled. Export tests use
real spawned processes and relational fixtures; GUI tests exercise actual Qt
processes, dialog cancellation, field selection, progress, failures and shutdown.
An additional disposable MySQL server with a SELECT-only export account verified
257 theories / 258 CSV rows across all 17 fields, exact decimal and JSON content,
byte-identical serial/parallel output, cancellation, missing-theory failure,
destination preservation and closed reader connections. The 7,457,190-byte fixture
took 0.214 seconds serially and 0.164 seconds with four readers; these are single
synthetic checks, not a prediction for the user's database. The temporary server
was shut down and removed; the user's database was not accessed.

## SQL central-charge filtering (18 September 2026)

`gui.theory_search.build_query` now evaluates every search condition in SQL.
Range endpoints are converted to `Decimal`, rounded to 30 places, and bound
directly in inclusive comparisons against the existing indexed
`central_charge_a_decimal` and `central_charge_c_decimal` columns. Ranges are
intentionally approximate; distinct fractions can share a decimal value.
Exact inputs still use reduced `Fraction` values, serialized by the shared JSON
helper into numerator/denominator pairs. SQL `JSON_CONTAINS` matches the requested
charge pair(s) against `central_charges_json`. For example, decimal `0.125` and
fraction `2/16` both compare as numerator 1, denominator 8.

The worker selects only theory IDs, with no charge payload or per-row Python
filter. Existing schema/index definitions, GUI controls, gauge matching,
streaming, and then-deferred CSV export were unchanged. A temporary MySQL 8.0.46
instance passed seven query cases and a real worker streaming check; `EXPLAIN`
confirmed range access through each existing decimal charge index. The temporary
server was removed; the user's MySQL database was not accessed.

Validation: unittest discovery ran **396 tests in 30.362 seconds: 358 passed,
38 skipped**, with existing live-database tests disabled. Tests distinguish exact
JSON matches from approximate range matches and verify that only IDs are fetched.

## Theory search controller and worker (17 September 2026)

The `search` tab now uses `gui.search_tab.SearchTabController`, launched by
`N2DatabaseWindow`, and the separate read-only Sage worker `gui.theory_search`.
Search combines all filled conditions. Cartan expressions accept comma-separated
product factors, double quotes for exact factor multisets, and semicolon-separated
alternatives. Factor order is ignored; repeated factors retain their multiplicity.
One realization must satisfy a gauge alternative, and each matching theory is
counted once regardless of its number of realizations.

Theory ID refers to `theories.id`. The initial implementation compared central
charges and bounds as `Fraction` values in Python; the 18 September update above
replaces this with SQL exact-JSON and approximate decimal-range comparisons.
Missing bounds are unlimited.
The nonempty-index checkbox requires only a non-whitespace full superconformal
index string, independently of Coulomb data or cutoff metadata. Empty conditions
include all theories, including ones without realizations or property rows.

The worker validates conditions before connection, disables schema initialization,
and streams one parameterized SELECT through a server-side cursor. It sends
matching IDs instead of index/input payloads to limit memory use. The controller
retains the immutable `theory_ids` tuple only after successful worker completion
and displays the count. `database` and `conditions` expose copied metadata without
the password. Filter changes, database changes/deletion, and another tab's work
invalidate this result; failures and cancellation never publish partial results.

Search becomes Cancel while active; window close cancels the read-only process.
Preferences and other tab operations are mutually excluded during work. Status
and error details appear in the result field's tooltip, with password-redacted
logs in `logs/log_search_YYYYMMDD_HHMMSS_ffffff.log`. Download is enabled for a
successful nonempty result. CSV writing was deferred at this stage and is now
implemented by the 18 September CSV update above.

Validation: **394 tests in 30.735 seconds: 356 passed, 38 skipped**, with live
MySQL testing disabled. New tests execute the search SQL against an in-memory
relational fixture and exercise actual Qt fixture processes, covering matching,
exact boundaries, repeated factors, no duplicate theories, protocol failures,
password redaction, cancellation and window integration. A real Sage worker CLI
check also rejected an invalid fraction before database access. No live MySQL
connection or user database write was performed during this implementation.

## TFORM threads and index CPU allocation (16 September 2026)

Settings now saves **TFORM threads** (`tools/form_threads`, default/minimum 1)
and a **TFORM executable** (`tools/tform_executable`, default `tform`). Both
full-index and Coulomb-index expansions use ordinary FORM at one thread and
TFORM with `-wN` above one. Existing preferences retain serial FORM by default.
The executable fields accept PATH names or full paths, including spaces.

The GUI index coordinator allocates
`min(selected_job_count, max(1, resolved_CPU_count // form_threads))` theory
workers. CPU cores `-1` resolves to the system logical CPU count, with fallback
one. Division rounds down; thread counts above the CPU budget still run one
theory with the requested TFORM count. Inner LiE cache generation stays at one
process per index worker; anomaly-build allocation is unchanged. Startup logs
include the index worker count and FORM thread count.

`common.form_utils.run_form` selects the executable and validates positive exact
integer thread counts. Full-index and Coulomb APIs propagate `form_threads` and
`tform_executable`; the full-index and database-index CLIs expose the matching
`--form-threads` and `--tform-executable` flags. Raw FORM programs and expansion
cache keys are unchanged, so existing results remain reusable across counts.

Validation: unittest discovery ran **378 tests in 29.536 seconds: 340 passed,
38 skipped**, with live MySQL testing disabled (`N2_TEST_MYSQL_DATABASE=`).
Coverage includes actual FORM/TFORM equality for connected/disconnected full
indices, exact fractional Coulomb PE/PL, cache reuse between engines, worker
allocation, and settings persistence after a fresh-process reload. The Settings
dialog was rendered and visually checked offscreen. A threaded full-index CLI
smoke check used temporary caches; database CLI validation was checked with the
connection mocked. Existing personal settings and MySQL contents were not changed.

## TFORM 32-worker benchmark evidence (16 September 2026)

Both cohorts ran one theory at a time through `t^18`, with the current
degree-bounded generator and `/usr/bin/tform -w32`, a 600-second limit, and
one observation per theory. Hardware was a Ryzen 9 7950X, 32 logical CPUs,
30.5 GiB RAM; TFORM was 5.0.0. GNU time includes startup, expansion and complete
text output to a local file, with compression afterward. It excludes Sage
preparation, output parsing, LiE projection, final polynomial construction and
database/cache operations. Inputs were read without schema initialization;
existing application data and caches were not changed.

- **16 repeated failures:** all succeeded, 23.94–38.33 seconds each, median
  across theories 25.19 seconds, total 473.42 seconds, peak TFORM RSS up to
  8.84 GiB. The retry log ran 32 theories concurrently and its start-to-timeout
  intervals cannot serve as an isolated serial baseline.
  `output/benchmarks/tform32_repeated_timeouts_20260916/report.md` retains the
  full table; the directory contains inputs, logs, hashes and compressed output.
- **16 randomly selected recovered cases:** sampled from 51 initial failures
  with explicit later full-index save messages, among 210 initial timeouts.
  The reproducible draw was
  `random.Random(3329612950807171883).sample(sorted(eligible_ids), 16)`.
  All succeeded in 9.00–15.22 seconds; total 188.91 seconds, median across
  theories 9.575 seconds, RSS 3.55–5.50 GiB.
  `output/benchmarks/tform32_recovered_random16_20260916/report.md` includes
  each later full-index save interval and both ratio columns.

The ratios use unrounded times:
`100 * T_TFORM / T_save` is 1.35–19.45%, and `T_save / T_TFORM` is
5.14–73.97. Historical save intervals include the whole index pipeline and
database save with 16 or 32 concurrent theories. These are elapsed-time
comparisons of different workloads, not controlled FORM or full-index
speedups. The benchmarks did not repeat gauge projection or serial-FORM
comparison for every case. Raw-program duplicates in the repeated-failure
cohort produced identical complete outputs.

`-w32` requests 32 TFORM workers; observed OS thread counts reached 63.
Peak RSS excludes the Sage parent, page cache and later projection/parsing.
The benchmark scripts overwrite their own output directories; copy them and
change their output paths before rerunning. No benchmarks were rerun during
the documentation refresh.

## Portability and LAN database discussion (assessment only)

The session assessed running on macOS and using this PC's database over a LAN;
neither deployment was tested and no server configuration was changed.
The second machine needs compatible Sage/PyQt6, FORM/TFORM and LiE, plus its
own executable/cache paths. A remote MySQL client must use the server's LAN
address over TCP instead of a local Unix socket. The server listener,
firewall and database-scoped account must permit the intended client.
The earlier server-listener observation was session-specific, not a current
configuration claim.

## Complete session PDF refresh (16 September 2026)

The canonical `output/pdf/n2_implementation_reference_summary.pdf` (54 pages)
and `index/n2_theory_index_Mathematical_Background.pdf` (26 pages) now cover
the completed index GUI, controller/logging refactor, multiprocessing and
deadlock handling, test relocation/permission fix, disconnected-sector reuse,
cache-file API, degree-bounded FORM, and password-confirmed Settings deletion
with its separate `.ui` layout. The index guide stays focused on index-package
algorithms; GUI/database operations are in the implementation reference.

The guide's FORM explanation is in sections 5.4-5.6, pages 5-7: bracket lookup,
module-local Skip, marker lifecycle, factorial induction, cutoff bounds and a
worked example. Sections 7.4-7.6 cover disconnected sectors, the singlet-once
rule, external flavor characters and stored-index reuse. SQLite size/WAL
guidance and dated performance evidence are included. Historical measurements
are distinguished from current implementation and documentation checks.

Both PDFs were rebuilt with resolved references and all 80 pages rendered for
layout review. Existing numbered equations and preserved guide pages 2-4 are
unchanged; the code listing matches the current FORM generator. Application
tests were not rerun during this documentation-only update. Retained sources,
new shared TeX inputs, build commands and QA details are in `output/pdf/BUILD.md`.

## Password-confirmed deletion in Settings (16 September 2026)

The MySQL section of `gui/settings.ui` now has **Delete all database contents…**.
`SettingsDialog` snapshots the displayed target (including unsaved edits) and
opens `gui.database_clear_dialog.DeleteDatabaseDialog`, which shows the server
or effective Unix socket, database and account, an irreversible-deletion warning,
an initially empty masked password field, and Delete/Cancel buttons. It requires
a nonempty freshly entered password. Opening or cancelling the warning does not
connect or delete anything. The entered password is never saved to preferences.

The confirmation layout is editable in `gui/database_clear_dialog.ui`, loaded
directly with `uic.loadUi`. Python retains the dynamic target and style-specific
warning icon, signal connections and worker behavior. The layout extraction
passed all **38 deletion-dialog and main-GUI tests in 1.477 seconds**; its
offscreen rendering was visually checked. These checks did not access MySQL.

After Delete, a separate Sage process (`gui.database_clear`) receives only the
connection target, entered password and explicit confirmation via stdin. It
authenticates a fresh connection with schema initialization disabled and invokes
`common.n2_theory_db.delete_all_theory_data`. That helper requires the current
schema and executes a transactional `DELETE FROM theories`; existing foreign-key
cascades remove all related property/realization/matter/flavor/index records.
Tables, schema metadata and SQLite caches remain. Failures roll back; credentials
and raw exception text are not published in worker responses.

Settings is unavailable while a build/search/calculation runs. During deletion,
the dialog disables input and cancellation and waits for the worker rather than
killing it on close. Wrong-password failures allow re-entry. An unconfirmed
worker/connection outcome is reported explicitly. Success clears retained index
jobs even if the Settings dialog is later cancelled. All tests for this action
live in `test/test_database_clear.py` and `test/test_gui_database_clear.py`.

Validation: the combined suite passed **370 tests in 65.584 seconds, no skips**.
An additional real-worker authentication/deletion regression added afterward
passed separately in 1.444 seconds. Live deletion tests used only the dedicated
test database; all data tables were checked after success, failed authentication
preserved records, and a failure before commit restored real cascading deletes.
The Settings and confirmation dialogs were rendered and visually reviewed offscreen.

## Degree-bounded FORM multiplication (16 September 2026)

`index.n2_theory_index._build_form_program` brackets the plethystic exponent
by t-degree. A marked term of degree k multiplies only exponent coefficients
of degrees 2 through N-k, using `itotal[t^idx1]`. `Skip J,itotal` preserves
the bracketed exponent between modules. A temporary `w` marker prevents newly
generated products from expanding again within the same exponential step;
terminal terms are retained by replacing `z` with 1. The existing `t(:N)`
bound remains. Orders 2 and 3 bypass the multiplication loop, and the public
API still returns the vacuum without FORM for orders 0 and 1.

The profiler in `test/benchmark_product_form.py` retains the former full-exponent
multiplication as `baseline`; `degree_bounded` now uses the production generator.
Its raw-program digest records the actual selected program. The new
`test/test_form_degree_bound.py` compares complete formal expansions for empty
and free sectors, vectors, simple/product matter, odd cutoffs and loop boundaries,
plus a connected bifundamental at the default cutoff 18.

Validation: `all_test.sh` passed **357 tests in 64.117 seconds, with no skips**,
including 43 complete formal-expansion comparisons and the existing live
database, GUI-worker, cache-reuse and disconnected-sector regressions.

A single sequential FORM-only comparison at order 18 gave **2.236 s baseline /
0.585 s optimized (3.82x)**, with exactly matching 28,154 formal terms. Both
ran FORM directly, without cache hits or singlet projection. This is an observed
example, not a general speedup guarantee; retained evidence is
`output/benchmarks/degree_bounded_form_20260916.json`.

Because FORM-cache keys are complete program strings, existing FORM entries
do not match the new generator. They remain intact; a new program is calculated
once on its first miss and reused afterward. Character-cache entries are unchanged.

## Character-cache file argument (15 September 2026)

`calculate_index`, `calculate_index_internal`, and their file wrapper now use
`char_cache_database_path=None` for the character SQLite database file. This
replaces both `cache_directory` and `database_path` in the index API. The index
and database-index worker CLIs use `--char-cache-database`; their old path
options are removed. The property wrapper's default is
`CHAR_CACHE_DATABASE_PATH`, initialized from `DEFAULT_CHAR_CACHE_DATABASE`.
GUI workers pass the existing `cache/character_database` file setting through
the new keyword. Default files and SQLite data need no migration: the character
database remains at the project root, and the FORM database defaults beside it.
The standalone character-cache class and builder keep their own existing API.

Validation: `all_test.sh` passed **355 tests in 60.129 seconds, with no skips**.
The tests include direct-file/module CLI invocation with custom filenames,
FORM-cache placement, property defaults/overrides and GUI worker forwarding.

## Disconnected-sector full indices (15 September 2026)

`index.n2_theory_index.calculate_index_internal` and `calculate_index` now
accept the optional keyword `theory_db_connection=None`, separate from the
SQLite `char_cache_database_path` and `form_cache_database_path` options. NetworkX builds
gauge connectivity from nontrivial hypermultiplet representations. Nonzero
multifundamentals join every charged factor; zero multiplicities are ignored.
Each connected component gets its own gauge factors and restricted hyper data.
All-gauge-singlet hypers form a separate free sector and contribute once.

The read-only `common.n2_theory_db.find_superconformal_index` helper uses the
existing canonical Lagrangian hash and accepts only a stored string index with
known `superconformal_index_order >= order`. Missing, JSON-null and unknown or
lower-precision results are misses. Factor IDs are ignored by this identity,
but the existing factor-order dependence remains; no hashes/schema are migrated.
Malformed polynomial strings or negative t degrees also trigger recalculation.
Free sectors have no stored gauge realization and use FORM directly.

Misses use the existing FORM/character-cache/singlet-projection implementation.
Identical sectors within one call reuse one calculated polynomial. Every sector
and intermediate product is truncated to the inclusive t cutoff using exact
Laurent coefficients. Output remains a flat `t,y,u` Laurent polynomial.
No new sector rows or sector indices are written. The borrowed connection is
never committed, rolled back or closed by index calculation. The database/GUI
worker passes its own connection through the property API, so reuse is enabled
without transferring a connection between processes. See
`test/test_disconnected_index.py` for algebraic and live MySQL regressions.

Validation: `all_test.sh` passed **353 tests in 58.615 seconds, with no skips**,
using the existing database-scoped test account, Sage/FORM/LiE and offscreen Qt.
This includes 14 new sector tests and the existing worker contention checks.
The previously slow theory 9780 (`SU(3) x SU(3)`, symmetric plus fundamental
matter on each factor) now completes through `t^18` in **13.753 seconds** with
one sector calculation and exact agreement with all 168 saved combined-index
monomials. This run used a private copied character cache, an empty FORM cache,
one process and no MySQL connection; see
`output/benchmarks/disconnected_sectors_20260915.json`.

The test-only `PROCESS` permission issue reported on 15 September was also
fixed: concurrency tests now observe their own connections' rollbacks,
including successful lock retries, instead of querying server-wide InnoDB
metrics. The preceding complete run passed 339 tests with the database-scoped
`n2_test` account. No global privilege grant is needed.

## Index GUI calculation (14 September 2026)

Both areas of the index tab are implemented. This section supersedes older
statements below that the tab is empty or its calculation action is unwired.
`IndexTabController` retains successful search results grouped by gauge factors;
**Calculate index** sends only checked groups' cached jobs to
`gui/index_calculator.py`. No second database-wide search is performed.

The Sage coordinator dynamically schedules one theory per task across spawned
workers, with the saved CPU limit and at most twice the worker count outstanding.
Each worker owns its connection, uses the initialized schema,
and calculates missing full/Coulomb indices and complete Coulomb spectra through
the existing backend. Exact cutoffs, both custom cache filenames, executables and
tool timeout are honored; inner full-index work uses one process.

The reusable `calculate_index_job` backend checks each retained theory/realization
ID for currently missing components. Initially writes used `missing_only=True`;
the 19 September update above adds known lower-cutoff upgrades with current-state
rechecks. Legacy unknown precision remains preserved. Each component commits independently.
Confirmed finished jobs are removed from the GUI list after the run; failed,
stopped and unconfirmed jobs remain selected for retry. **Stop** and window close
let active components finish/save, cancel queued work, and wait for clean worker
shutdown. A manager queue carries live logs without child feeder-thread shutdown
dependencies. Searches and calculations each create a flushed, redacted
`logs/log_index_YYYYMMDD_HHMMSS_ffffff.log` through the shared GUI logger.

Index transactions and existing-theory anomaly attachments now lock the parent
theory before property/realization rows, avoiding a child/parent lock-order cycle.
Index writes and cutoff recording retry MySQL 1205/1213 at most twice after full
rollback, rechecking current values without recalculation. Connection errors or
failed rollbacks are not replayed. The earlier anomaly fresh-property gap-lock
fix remains in place; index calculations only update existing property rows.
Other database clients can still introduce contention, so bounded retries remain
necessary. See `gui/README.md` for behavior and `test/README.md` for test commands.

Validation: combined discovery passed **338 tests in 55.104 seconds, with no
skips**, using Sage/FORM/LiE, offscreen Qt and a password-protected disposable
MySQL 8.0.46 server. This includes actual GUI search-to-calculation/file logging,
simple/product indices, exact fractional cutoffs, custom caches, stale retries,
partial failures, cooperative Stop, abrupt worker exit and connection cleanup.
The 256-theory/eight-worker index stress run and targeted parent-row contention
test each added **zero InnoDB deadlocks**. No production data was modified.
The temporary server was shut down after validation. PDFs were not rebuilt.

## Test layout update (14 September 2026)

All new test code belongs in `test/`, including GUI tests and test helpers.
The former GUI test modules are now `test/test_gui_n2_db.py`,
`test/test_gui_index_tab.py`, and `test/test_gui_logging_utils.py`.
The opt-in keyring check is `test/check_secret_service.py`. Test discovery
under `test/` now includes the GUI suite; `all_test.sh` already uses that path.
See `test/README.md` for combined and focused commands. Older validation counts
below remain dated snapshots from before this test consolidation.

After relocation, combined discovery ran **316 tests in 21.616 seconds**:
**292 passed and 24 live-MySQL tests skipped** with no test database configured.
The relocated opt-in Secret Service check also passed using its private D-Bus
session and disposable keyring.

## Current session: anomaly GUI and concurrent imports (14 September 2026)

The anomaly tab is now functional. This section and the current GUI, MySQL,
Tests and Documentation sections supersede the older shell/empty-tab and Git
snapshots below. The index tab remains empty; the separate backend index worker
is implemented. The session added:

- Saved full-index order (default `18`), exact Coulomb cutoff (default `90`),
  and CPU limit (`-1`, all logical system cores), with persistence and validation.
- The two-column anomaly layout, editable gauge lists, multi-file UTF-8 loading,
  simple/product enumeration, SCFT checks, database insertion and optional
  character-cache preparation through `full_max_order // 2`.
- A responsive Sage build process, cooperative Stop, per-group/overall counts,
  error reasons, password redaction, and timestamped/leveled GUI and file logs
  under project-root `logs/log_anomalies_{datetime}.log`.
- Spawned candidate workers with dynamically bounded scheduling, separate
  MySQL connections, serial schema setup, atomic imports, duplicate-race handling
  and bounded transaction retries.
- Test-harness fixes for the configured CLI password/socket and connection
  cleanup checks that ignore unrelated clients while detecting owned leaks.
- A database deadlock fix: fresh theories insert properties directly instead
  of locking a missing properties row; existing-theory attachments retain locks.

The final implementation run passed **268 backend tests in 33.518 seconds,
without skips**, against a password-protected disposable MySQL 8.0.46 server
with actual Sage/FORM/LiE. The fixed synchronized-pair and 256-candidate,
eight-worker checks added zero InnoDB deadlocks. Before the fix, the controlled
256-candidate run exhausted retries on 93 inserts; afterward all 256 inserted.
During this documentation refresh, **32 GUI tests passed in 0.869 seconds**
offscreen. No production database or personal keyring was used for validation.
The temporary server was shut down after the backend run.

Both summary PDFs and their retained sources are refreshed for this
session; see Documentation and `output/pdf/BUILD.md` for the final artifacts.
The implementation reference owns the property/database and GUI explanations;
the index guide is restricted to index-package algorithms and validation.
The B/D-to-Spin naming discussion did not change global-form support or migrate
stored groups. No new index-tab actions, HL/Higgs calculation or global quotient
data model were implemented.

## Two-stage database workflow (12 September 2026)

`calculate_n2_theory_properties` and database imports now calculate/store only
anomaly-checked basic properties. Index and Coulomb-spectrum work is explicit
and separate. `common/n2_theory_db_indices.py` fills missing components, or
upgrades known lower cutoffs with `--upgrade`. Successful components commit
individually; failures are reported and can be retried.

MySQL schema 7 adds nullable full-index and exact Coulomb cutoff metadata.
Updates lock and recheck the shared theory row, compare the two cutoffs
independently, and preserve equal/lower-order inputs and legacy indices whose
precision is unknown. `record_lagrangian_index_cutoffs` can record verified
original cutoffs for legacy results before upgrades. No precision is inferred
from the largest nonzero term. See the current MySQL section below for usage.
The existing user database has not been migrated during this implementation;
validation used a separate temporary MySQL 8.0.46 instance. The full backend
suite passed all **238 tests in 21.920 seconds**, including live MySQL migration,
concurrent upgrades, rollback, partial retries, and real FORM/LiE calculations.
The test command was `sage -python -B -m unittest discover -s test -v`, with
`N2_TEST_MYSQL_DATABASE` and `N2_TEST_MYSQL_UNIX_SOCKET` pointing exclusively to
the disposable test instance, `DOT_SAGE=/tmp/codex-sage-cache`, and bytecode
generation disabled. The GUI was not changed or retested in this update.

## Project rename and GUI update (11 September 2026)

The project and GitHub repository are named **N2SCFTDB**. The repository URL is
`https://github.com/SansyHuman/N2SCFTDB` and the local checkout is
`/home/subo-lee/PycharmProjects/N2SCFTDB`.

At that snapshot, code was committed through `f96b16c` (GUI shell). The `gui/` directory
contains the editable `n2_db.ui` and `settings.ui` forms, a PyQt6 launcher,
per-user settings, and native keyring password storage. Both tabs were empty
then; the anomaly tab is now functional as described above. That GUI had
18 passing isolated regressions and a passing
disposable Linux Secret Service integration check from the GUI implementation
session; these counts are separate from the backend suite below.

The rename updates project labels, IDE module references, portable benchmark
script roots, and both PDF sources and outputs. The GUI retains its established
settings directory and keyring service identity so existing saved preferences
and credentials remain accessible; cache paths under the former checkout are
rebased to the renamed project when loaded. See `gui/README.md` for details.
The rename does not commit, stage, push, or change Git history. Its validation
passes all 20 GUI regressions, including relocation and external-cache-path checks.
The rebuilt PDFs retain 34 and 20 pages, with body text unchanged apart from
project branding and no unresolved references or overfull boxes.

The property and MySQL sections below describe the current two-stage workflow.
Older implementation narratives, benchmarks, and PDF descriptions retain their
explicitly dated snapshots; the later PDF update described under Documentation revises the property/database
chapters while retaining dated benchmarks.

## Retained index/cache session snapshot (10 September 2026)

That code snapshot was committed through `04bbe21`. That session added three
changes to the FORM/LiE index pipeline:

1. `index/form_expansion_cache.py` serializes exact `IndexFormTerm` lists in
   SQLite, keyed by the complete raw FORM program. Index calculations now reuse
   those expansions, including across different groups when the programs match.
2. The character cache reuses two existing lower-order decompositions when this
   avoids missing intermediate products. First Adams and trivial-representation
   identities bypass LiE. The temporary candidate was compared for exactness and
   performance before promotion.
3. `build_decomposition_cache` precomputes every same-irrep Adams product at
   weighted orders 1 through a maximum. It uses `frobenius_solve`, a persistent
   process pool, and a commit barrier between orders. The API and CLI support
   resuming partially filled databases.

For an index through `t^N`, prebuilding through weighted Adams order
`floor(N/2)` is sufficient for each required adjoint/matter irrep, including
its distinct conjugate when used. For `t^18`, use order 9. This can overcompute:
the singlet projector only needs a subset of all same-irrep products.

With character decompositions and final singlet coefficients already filled,
FORM hits sped up the nine measured full-index cases by 6.52-8.44 times at
`t^18`; all 270 timed comparisons matched exactly. The cached-factor planner
improved targeted misses but showed no consistent full-index speedup. Its
operation-count savings must not be presented as a general end-to-end gain.
The documentation-time verification ran **209 tests: 207 passed and two
live-MySQL tests skipped**. The Tests section records the command and timing;
the benchmark sections give conditions and retained evidence.

Earlier work remains implemented: simple/product conformal matter enumeration,
Tits reality classification, exact sparse singlet projection, Coulomb-branch
PE/PL and spectrum extraction, and MySQL schema version 6 with full/half-hyper
normalization. Old realization hashes are not automatically rehashed.
**No SU power-sum backend, symbolic-multiplicity FORM template, HL/Higgs-branch
calculator or flavor-refined index has been implemented.** The Mathematica and
HL/Higgs discussions are context, not authorization for future code changes.

Both existing PDFs and their retained LaTeX/build inputs are updated in this
turn for the FORM cache, planner, complete builder, cutoff proof and measured
validation. This turn changes documentation only.

The user prefers exact arithmetic, FORM for symbolic expansions, reuse of
existing helpers, and gauge information supplied through `GaugeFactorData`.
Their conjugacy preference is the lexicographically larger Dynkin labels
whenever identifying conjugates, including simultaneous conjugation for
product groups. Actual character decompositions retain distinct orientations.
Treat this document as context; future work depends on the user's next request.

## Project location and status

Project root:

```text
/home/subo-lee/PycharmProjects/N2SCFTDB
```

The project studies four-dimensional N=2 Lagrangian superconformal field
theories. It currently provides anomaly and conformality checks, common theory
properties, superconformal/Coulomb-index calculation, Coulomb-generator
dimensions, bounded irrep enumeration, simple- and product-group conformal matter
enumeration, and MySQL persistence.

At the 14 September documentation refresh, HEAD is `fc34ab4` (parallel SCFT
candidate checks), following the GUI/build/log commits. The fresh-properties
deadlock fix and its regressions are local changes on top of that commit.
Recheck Git before using this snapshot as current state. This documentation
update revises the handoff, both canonical PDFs and their LaTeX/build inputs;
it performs no commit, staging change or remote fetch. Pre-existing `main.py`,
gauge-list files and staged/untracked bytecode are left alone. Generated SQLite
caches, logs and bytecode are not source changes.

## Runtime dependencies

The active implementation requires:

- SageMath; the code was developed with SageMath 10.7.
- NetworkX for disconnected gauge-sector partitioning (3.6.1 in the Sage environment).
- FORM, available as the `form` executable.
- LiE, available as the `lie` executable.
- PyMySQL and a MySQL server for database operations.
- OR-Tools (`ortools`) for `common/math_utils.py`, the theory enumerator and
  `build_decomposition_cache`. The builder imports the solver lazily.
  `cp_model` is imported at module scope, so importing `common/n2_theory_iter`
  requires OR-Tools even before calling the solver. It is not required by the
  standalone anomaly/index APIs. The configured Sage environment has OR-Tools
  9.15.6755.

Run Python entry points and tests with Sage's Python environment, for example:

```bash
sage -python -m unittest discover -s test -p 'test_*.py' -v
```

The working command in this local environment was:

```bash
DOT_SAGE=/tmp/codex-sage-cache \
  /home/subo-lee/miniconda3/envs/sage/bin/sage -python \
  -m unittest discover -s test -p 'test_*.py' -v
```

FORM and LiE are available as `/usr/bin/form` and `/usr/bin/lie`. The `/tmp`
Sage directory is runtime scratch space and may need to be recreated later.

## Shared utilities

- `common/form_utils.py`: `run_form`, `split_top_level`, and
  `split_signed_terms`. FORM executes in an isolated temporary directory with
  timeout and error handling. The full-index and Coulomb modules reuse these.
- `common/number_utils.py`: exact integer and rational validation.
  `as_integer` and `as_nonnegative_int` reject Python floats, including `1.0`,
  and booleans. Rational dimension inputs should use `Fraction`, Sage `QQ`,
  integers, or accepted rational strings, rather than floats.
- `common/json_utils.py`: exact `Fraction` serialization as
  `{"numerator": n, "denominator": d}`; tuples serialize as JSON arrays.
- `common/math_utils.py`: `frobenius_solve(coefficients, target,
  max_solutions=None)` enumerates nonnegative integer solutions of one linear
  equation with positive integer coefficients. It reduces by the coefficient
  gcd, bounds each variable by `target // coefficient`, and uses OR-Tools
  CP-SAT with one worker and an all-solutions callback. Rational equations
  must first be scaled to integers. `max_solutions` permits a partial result;
  CP-SAT integer limits apply. The low-level integer conversion uses
  `operator.index`, which also accepts booleans, unlike `number_utils`.
- `frobenius_system_solve(coefficients, targets, max_solutions=None)` in the
  same module solves a rectangular system with nonnegative integer
  coefficients. Zero coefficients are allowed; each variable must have a
  positive coefficient somewhere to give a finite bound. It reduces each row
  by its GCD and bounds each variable by the minimum target/coefficient over
  the rows in which it appears. Empty systems have zero variables and return
  `[()]`; inconsistent systems return `[]`. Coefficients of fixed-zero
  variables are omitted from the CP-SAT model, including oversized integers.
  Both solvers share `_solve_frobenius_model` and `_SolutionCollector`.

## Common JSON input format

A simple gauge group is represented by:

```json
{
  "algebra": "A1",
  "hypermultiplets": [
    {
      "representation": "fundamental",
      "number": 4,
      "kind": "full"
    }
  ]
}
```

A product gauge group is represented by:

```json
{
  "gauge_groups": [
    {"id": "left", "algebra": "A1"},
    {"id": "right", "algebra": "A1"}
  ],
  "hypermultiplets": [
    {
      "representations": {
        "left": "fundamental",
        "right": "fundamental"
      },
      "number": 2,
      "kind": "full"
    }
  ]
}
```

Conventions:

- Dynkin labels use Bourbaki numbering.
- Supported simple algebras are `A_r`, `B_r`, `C_r`, `D_r`, `E6`, `E7`,
  `E8`, `F4`, and `G2`.
- Gauge factors are assumed to be simply connected.
- Type `C_n` uses the group notation `Sp(n)`, not `USp(2n)`.
- The default hypermultiplet kind is `full`.
- Half hypermultiplets are accepted only in pseudoreal representations.
- Omitted factors in a product representation are treated as singlets.
- Representations can be given by supported names or explicit Dynkin labels.

Example input files are in `anomalies/`.

## Lie-algebra backend

File: `anomalies/lie_algebra.py`

This module uses SageMath for:

- Cartan and root-system data.
- Lie-algebra dimensions and dual Coxeter numbers.
- Representation dimensions.
- Quadratic Casimirs and Dynkin indices.
- Conjugate representations.
- Exact coroot data for reality classification by Tits' formula.
- Named-representation Dynkin labels.

`SimpleLieAlgebra` stores the Sage objects and derived invariants for one
simple algebra. Expensive algebra and representation calculations are cached.

`representation_reality` first compares the validated labels with their
conjugate; a non-self-dual irrep is `"complex"`. For a self-dual highest weight:

```text
FS(lambda) = (-1)^<lambda, 2 rho^vee>,
2 rho^vee = sum_(positive roots alpha) 2 alpha / <alpha,alpha>.
```

`_tits_parities` caches `<omega_i, 2 rho^vee> mod 2` for each algebra. A dot
product with the Dynkin labels then gives `"real"` for even parity and
`"pseudoreal"` for odd parity. All arithmetic is exact, including for unequal
root lengths; no symmetric/exterior squares are computed. The formula is
Theorem 3.6 of https://arxiv.org/abs/0704.0165.

The index normalization is `T(adjoint) = C2(adjoint) = h_dual` and
`T(SU(N) fundamental) = 1/2`. For type A, `quadratic_casimir` relies on
`character.highest_weight()` to project the ambient weight to the traceless
subspace. Replacing this with a raw sum of ambient fundamental weights would
be incorrect: the SU(5) fundamental would give `T = 15/16` instead of `1/2`.
The same common-coordinate shift does not affect the coroot pairings in
Tits' formula.

The accepted low-rank conventions avoid redundant simple-algebra names: use
`A1` for `B1` or `C1`, `A3` for `D3`, and a product of `A1` factors for `D2`.
Both `B2` and `C2` remain accepted even though they are isomorphic.

## Anomaly and conformality checker

File: `anomalies/check_n2_anomalies.py`

The checker handles simple and product gauge groups. It checks:

- Input shape and representation validity.
- Whether every half hypermultiplet is pseudoreal.
- Perturbative gauge anomalies.
- The conventional mod-two Witten anomaly for `A1`, `C_n`, and the
  isomorphic `B2` case.
- The one-loop beta function of every gauge factor.
- Spectator-dimension factors for product representations.

For gauge factor G_i, the one-loop coefficient is

```text
b_0,i = 2 h_i^vee
        - sum_H n_H (2 epsilon_H) T_i(R_H,i)
          product_(j != i) dim(R_H,j),
```

where `epsilon_H` is 1 for a full hypermultiplet and 1/2 for a half
hypermultiplet. Thus a full hyper contributes `2T` and a half hyper contributes
`T`, including spectator dimensions. For SU(N) with `N_f` full fundamentals,
this gives `b_0 = 2N - N_f`. The previous handoff omitted the factor of two;
the implementation already used the correct formula.

With `I_i(R) = T_i(R_i) * product_(j != i) dim(R_j)`, the conventional Witten
parity is `sum_(half H) n_H * 2 I_i(R_H) mod 2`. Half-hyper multiplicities
need not be even separately; the weighted total determines the anomaly.
For valid matter, `b_0,i = 0` implies this parity vanishes: doubling the beta
equation leaves an even vector/full-hyper contribution. This implication
applies to the conventional anomaly on spin manifolds checked here. The
parity test remains useful for nonconformal inputs. See also section 2 of
https://arxiv.org/abs/1309.5160.

The checker does not require pure flavor 't Hooft anomalies to vanish. It also
does not check global anomalies that depend on a non-simply-connected quotient
of the gauge group.

CLI example:

```bash
sage -python anomalies/check_n2_anomalies.py anomalies/example_e6.json
```

## Representation and theory enumeration

File: `common/n2_theory_iter.py`

```python
enumerate_irreps(gauge_factor, *, max_index=None, inclusive=True,
                 include_singlet=False, identify_conjugates=True)
enumerate_product_irreps(gauge_factors, *, max_indices=None, inclusive=True,
                         include_singlet=False, identify_conjugates=True)
enumerate_simple_theory_candidates(gauge_group)
enumerate_product_theory_candidates(gauge_groups)
```

The irrep APIs accept `GaugeFactorData` objects. The simple API returns sorted
`(DynkinLabels, Fraction)` pairs. The product API returns
`(labels_by_factor_id, indices_by_factor_id)` pairs; its indices already include
all spectator dimensions. `max_indices` overrides bounds by factor ID.

Every automatic bound is **`2 * dual_coxeter_number`**, inclusive by default.
This safely includes irreps usable as half hypers before their reality is
known. Full hypers must subsequently obey the stronger cost constraint
`2T <= 2h_dual`. `inclusive=False` makes the selected bound strict.

The simple search increments Dynkin labels and prunes branches above the
index bound. The product search also prunes using partial spectator indices.
Individual factor singlets are always allowed in product representations;
`include_singlet` controls only the representation trivial under the entire
gauge group. The global singlet is excluded by default.

`identify_conjugates=True` retains the lexicographically **larger** label
tuple, favoring lower-numbered Dynkin nodes, such as the SU(N) fundamental.
Conjugacy filters the output, not the search branches. For product groups it
identifies only simultaneous conjugation of every factor; independently
discarding factor conjugates would lose representations such as `(3, 3bar)`.
The property and database canonicalizers use the same larger-tuple convention.
Explicit conjugation and the full-hyper index calculation still retain the
actual conjugate representation where mathematically required.

`enumerate_simple_theory_candidates("A2")` accepts a Cartan-type string and
returns input-schema dictionaries with `algebra` and `hypermultiplets`.
Pseudoreal irreps are counted in half-hyper units; real and complex irreps
are counted as full hypers. It scales exact rational costs by an LCM and calls
`frobenius_solve` to solve

```text
sum_R multiplicity_R * cost_R = 2 h_dual,
cost_R = T(R) for pseudoreal R, otherwise 2 T(R).
```

Terms with zero multiplicity are omitted. Irreps whose cost exceeds the budget
have multiplicity zero. The function enumerates conformal matter candidates
without explicitly invoking the anomaly checker; it omits free singlet matter.
It returns only the half-hyper description for pseudoreal matter, avoiding a
duplicate full/half description within its output. The database independently
identifies equivalent descriptions by pairing half hypers into full hypers,
with at most one remaining half hyper per pseudoreal gauge representation.

Verified candidate counts are A1: 2, A2: 3, G2: 2, C3: 8, and A32: 6.
A1 gives eight half fundamentals or one full adjoint. A32 (SU(33)) reduces to
`n_fund + 31*n_antisym + 35*n_sym + 66*n_adj = 66`.
Measured A32 times were about 5.48 seconds including initial algebra setup
and 1.12 seconds with the algebra cached. These are local measurements, not
performance guarantees. Before the Tits change, the first reality check alone
exceeded a 40-second profiling cutoff.

`enumerate_product_theory_candidates(["A1", "C2"])` accepts any nonempty
iterable of Cartan strings, including a one-shot iterator. A bare string is
rejected. Factors receive IDs `gauge_1`, `gauge_2`, ... in input order, and
Cartan names are normalized with `get_lie_algebra`. Repeated types and single
factors are supported. The output uses the product input schema with
`gauge_groups` and `hypermultiplets`; representation values are lists, so the
checker accepts the dictionaries directly without a JSON round trip.

It reuses `enumerate_product_irreps`, caches factor reality by Cartan type and
Dynkin labels, and determines whole-product reality. Each irrep contributes
`T_a(R)` per half hyper if pseudoreal, otherwise `2*T_a(R)` per full hyper.
Irreps exceeding any factor's budget are dropped. The remaining exact rational
beta equations are scaled to integers independently by row and solved with
`frobenius_system_solve`. Odd half-hyper counts are retained. There is no anomaly
check in the enumerator itself; free gauge singlets and zero counts are omitted.
Both coupled and decoupled theories are included, and factor permutations are
not identified. SU(2) x SU(2) has exactly eight candidates for labelled factors.
Tests compare mixed-group results to exhaustive rational enumeration and
single-factor results to the existing simple enumerator.

## Theory properties

File: `common/n2_theory_properties.py`

`calculate_n2_theory_properties(data)` returns only basic properties:

```python
{
    "group": ...,
    "lagrangian_scft_candidate": ...,
    "flavor_symmetry": ...,
    "conformal_manifold_dimension": ...,
    "exactly_marginal_gauge_couplings": ...,
    "central_charges": ...,
    "disconnected_sectors": ...,
}
```

`calculate_n2_theory_indices(data, order=18, max_dimension=90)` separately
returns `superconformal_index`, `coulomb_branch_index`, and
`coulomb_branch_spectrum`, plus `superconformal_index_order` and
`coulomb_branch_index_max_dimension`. The cutoffs are inclusive; full-index
order is an integer t power, while the Coulomb cutoff is an exact rational
scaling dimension. Store these requested cutoffs even when boundary terms
vanish. The complete spectrum is independent of either cutoff.

Both index values are serialized strings. The spectrum is a sorted tuple of
`Fraction` dimensions with repetitions preserved. For non-SCFT candidates,
the five index-related fields are None; basic central charges and conformal
manifold dimension are also None. The basic call never calculates indices or
spectra. Its CLI defaults to basic properties; `--indices` selects the second
stage and accepts `--index-order` and `--coulomb-max-dimension`.

### Flavor symmetry

Hypermultiplets are grouped by their complete irreducible gauge
representation. For `n` identical full hypers, the connected flavor group is:

- `U(n)` for a complex gauge representation.
- `Sp(n)` for a real gauge representation.
- `SO(m)` for a pseudoreal gauge representation, where `m` counts half-hyper
  units and one full hyper contributes two units.

The complex representation and its conjugate are assigned to the same flavor
block. For a product gauge group, reality is the reality of the external tensor
product of all factor representations.

### Conformal manifold

For a Lagrangian SCFT candidate, the current implementation counts one exactly
marginal gauge coupling for each simple gauge factor:

```text
dim_C M_conf = number of simple gauge factors.
```

For a nonconformal input, this dimension is `None`.

### Central charges

Let `n_v` be the total dimension of the gauge algebra and `n_h` the effective
hypermultiplet dimension. The implementation uses exact fractions:

```text
a = (5 n_v + n_h) / 24,
c = (2 n_v + n_h) / 12.
```

The returned `central_charges` payload contains only `a` and `c`; the effective
counts `n_v` and `n_h` are internal calculations, not returned properties.

### Index integration

The property calculator computes the superconformal index through `t^18` by
calling `calculate_index_internal`. This internal API accepts already parsed
gauge factors and hypermultiplets, so the index code does not repeat anomaly
validation.

The property-calculation cache is intentionally stored at the project root:

```text
/home/subo-lee/PycharmProjects/N2SCFTDB/char_decomposition_cache.db
```

The property calculator computes the Coulomb-branch index through scaling
dimension 90 by passing the parsed gauge factors to
`calculate_lagrangian_coulomb_branch_index`.

The user's `_calculate_coulomb_branch_spectrum(anomaly_result)` obtains Weyl
invariant degrees from the same gauge factors and converts them to `Fraction`.
It is called by `calculate_n2_theory_indices`, separately from basic properties. Public convenience APIs
include `calculate_central_charges`, `calculate_superconformal_index`,
`calculate_coulomb_branch_index`, and `calculate_coulomb_branch_spectrum`.

## Superconformal-index implementation

Files:

- `index/n2_theory_index.py`
- `index/char_decomposition_cache.py`
- `index/form_expansion_cache.py`

The original Mathematica and pure-Sage implementations were removed. The
current implementation supports both simple and product gauge groups and uses:

- FORM to expand and collect the truncated representation-valued plethystic
  exponential on a raw-program cache miss; SQLite reuses the parsed expansion
  on a hit.
- LiE to perform Adams operations and intermediate tensor-product decompositions.
- Exact sparse pairing of dual irreps to extract the final gauge singlet.
- A process pool to generate independent cold-cache decompositions in
  parallel.

For product gauge groups, FORM maintains separate formal characters for each
factor, and singlet projection is performed factor by factor.

Flavor fugacities are effectively set to 1: matter copies enter as integer
multiplicities in `_matter_character_multiplicities`. This preserves total
counts but omits flavor representation information. The separate flavor-group
metadata in the property calculator does not make the index flavor-refined.

### FORM expansion cache

`FormExpansionCache` in `index/form_expansion_cache.py` owns the moved parser
and `IndexFormTerm` record. The frozen record has `coefficient: Fraction`,
integer `t_power`, `y_power`, `u_power`, and
`characters: tuple[(character_index, AdamsPowers), ...]`.
`_encode_expansion` serializes a list of rows:

```text
["numerator", "denominator", t_power, y_power, u_power,
 [[character_index, [n1, n2, ...]], ...]]
```

`_decode_expansion` reconstructs arbitrary-size exact fractions and nested
tuples. `FormExpansionCache.parse_form_output` replaces the old parser in
`n2_theory_index.py`; the current term class is `IndexFormTerm`, not `FormTerm`.

```sql
CREATE TABLE form_expansions (
    program TEXT COLLATE BINARY NOT NULL PRIMARY KEY,
    expansion_json TEXT NOT NULL
) WITHOUT ROWID;
```

The full raw FORM program string is the primary key, not a digest. Whitespace,
numeric multiplicities, character numbering and order all affect equality.
There is no canonical program normalization or symbolic-multiplicity template.
`get_expansion(program)` selects and decodes a hit. On a miss it runs FORM,
parses the output, encodes it, and inserts with `ON CONFLICT DO NOTHING`.
Execution/parse failures are not cached. Values contain formal characters
before gauge projection; Cartan types and Dynkin labels are supplied by each
calculation's character basis afterward.

Connections are local to each thread, with PID checks to avoid reusing an
inherited connection. WAL, a 30-second busy timeout and three-attempt busy/locked
retries support concurrent clients. FORM runs outside write transactions.
Concurrent identical misses may execute more than once, but store one complete
row. This is safe concurrent persistence, not a single-execution guarantee.
The context manager closes the current thread's connection.

The default is project-root `form_expansion_cache.db`, from
`DEFAULT_FORM_CACHE_DATABASE`. Unlike the character module, this module does
not set `user_version`. `calculate_index` and `calculate_index_internal` accept
`form_cache_database_path=`; the CLI accepts `--form-cache-database`. By default
this database follows the actual character database directory. Thus
`--char-cache-database /path/characters.db` selects the character database
file and puts the default FORM file beside it.
A FORM hit avoids execution and parsing but still performs singlet projection
and polynomial construction. Orders below two return the vacuum without
external execution or cache access.

### FORM cache measurements and cross-theory reuse

The retained I/O benchmark used 113,831 terms from a `t^18` expansion: 589 bytes
of program text and 5,907,568 bytes of JSON (5.91 MB). Thirty measured trials
followed three warmups, with SQLite WAL/synchronous FULL on the project
filesystem. Median save time was 221.51 ms (179.32 ms serialization and
42.07 ms insert/commit, with component medians measured separately). Initial
DB setup was another 31.43 ms. SQL read took 3.08 ms; decoding took 866.42 ms.
A hit cost 870.81 ms with an open connection or 894.65 ms with a new client
including close. Python object reconstruction dominated. This excludes FORM
execution/parsing and measures warm filesystem caches, one process and no
writer contention. Evidence and reproduction script:
`output/benchmarks/form_expansion_cache_io/{report.md,timings.json,benchmark.py}`.

Full-index benchmarks used nine SCFTs, `t^12` and `t^18`, one warmup and five
measured repetitions per mode. Both decompositions and final singlet
coefficients were prefilled; query-only character databases and no-LiE guards
verified this. Each timed calculation opened fresh clients. All 270 timed
indices matched exactly. Median full times at `t^18`, in seconds:

| Theory (full hypers) | FORM cache disabled | Miss + save | Hit | Disabled / hit |
|---|---:|---:|---:|---:|
| SU(2), 4 fundamentals | 0.4323 | 0.4916 | 0.0617 | 7.00x |
| SU(3), 6 fundamentals | 1.7220 | 1.7959 | 0.2304 | 7.48x |
| SU(5), 10 fundamentals | 1.7265 | 1.7839 | 0.2648 | 6.52x |
| SU(3), 1 adjoint (N=4) | 0.2808 | 0.3180 | 0.0429 | 6.54x |
| Sp(2)=USp(4), 6 fundamentals | 0.4434 | 0.4817 | 0.0634 | 7.00x |
| Spin(8), 6 vectors | 0.4329 | 0.4848 | 0.0655 | 6.60x |
| G2, 4 fundamentals | 0.4418 | 0.4874 | 0.0640 | 6.90x |
| SU(2)xSU(2), 2 bifundamentals | 2.7421 | 2.7843 | 0.3251 | 8.44x |
| SU(5), symmetric + antisymmetric | 13.7565 | 14.1896 | 1.8771 | 7.33x |

Here "disabled" bypasses FORM SQLite but still executes/parses FORM; it is
benchmark instrumentation, not a public CLI mode. Miss mode uses an initialized
empty FORM DB and includes encode/write time. Hit mode forbids FORM execution.
Setup, prefilling and Python/Sage startup are excluded. Runs use one worker,
warm OS file caches and no competing benchmark workers. The `t^12` speedup
range was 3.23-6.92x. Reports, scripts and raw samples are in
`output/benchmarks/form_expansion_theories/`.

That directory also retains `check_cross_theory_reuse.py`,
`cross_theory_reuse.json` and `cross_theory_reuse.md`. SU(2) with four
fundamentals shares a raw program with G2 with four fundamentals; Sp(2) with
six fundamentals shares one with Spin(8) with six vectors. Both directions at
`t^12` and `t^18` gave eight passing checks. After the first theory populated
one row, a new second-theory client executed/parsed FORM zero times, decoded
once and retained one row. Each final index matched its own fresh-FORM result,
while paired final indices differed. A changed-program control missed.
Cross-theory reuse requires identical raw text and a shared FORM database;
matching expansion structure alone is not sufficient.

### Character decomposition cache

`CharacterDecompositionCache` stores decompositions and final singlet coefficients
in SQLite using Python's `sqlite3`. Both standalone index calls and property
calculations default to `char_decomposition_cache.db` at the project root.
The generated database and its journal sidecars are ignored by git.

`character_decompositions` has a composite primary key of Cartan type, input
Dynkin labels and canonical Adams powers, plus weighted Adams order and a JSON
payload of output labels and signed decimal-string coefficients.
`singlet_coefficients` keys the Cartan type and canonical product description,
storing a signed decimal-string integer. SQLite `user_version=1` records the
cache schema/convention version, independently of the theory MySQL schema.

`get_singlet_multiplicities(cartan_type, rank, products)` accepts each product
as a sequence of `(labels, Adams powers)` pairs and looks up the scalar before
requesting any decompositions. Repeated labels merge; factors sort in descending
order; actual conjugate orientations remain distinct. The existing
`singlet_multiplicities` API for decomposed virtual characters also persists
results, using a distinct key namespace. Zero and negative results are valid.

Connections and memory caches are local to each thread. Process workers open
their own connections; the process-pool parent batches completed writes by
dependency level. Sequential generation flushes at 64 completed entries and at
batch completion. WAL and short transactions with bounded busy retries support
concurrent clients. LiE never runs inside a write transaction. Context-manager
use closes connections explicitly. The full-decomposition API remains available;
singlet requests now use the optimized projection described below.

`CharacterDecompositionCache` and its builder retain `cache_directory=` for
a directory and `database_path=` for a file; the builder CLI retains
`--cache-directory` and `--cache-database`. These are mutually exclusive.
Index calculation instead uses only `char_cache_database_path=` and
`--char-cache-database`, both selecting a file. Cache lookup
uses memory and SQLite only; missing entries use LiE decompositions and exact
singlet pairing as needed. Legacy
JSON import, directory discovery and the `cache_path()` compatibility wrapper
have been removed. Existing SQLite entries remain valid with no schema change.
Old JSON files are no longer consulted; the cleanup does not delete them.

The candidate was implemented under `/tmp/sci-sqlite-cache/candidate`, compared
against the original, and promoted only after exact agreement and measured
speedups. For A8 SQCD through t^18, three-trial median complete-index times were
8.71 to 6.17 seconds cold and 4.19 to 1.71 seconds warm. Some short cold runs
incur 30–50 ms of setup overhead. All 2,343 persisted decomposition comparisons
and all seven benchmark index cases matched. Details and raw timings are in
`output/benchmarks/sqlite_cache_benchmark.md` and `sqlite_cache_timings.json`;
`test/benchmark_character_cache.py` reproduces comparisons with baseline git
revision `d4d103a`. The historical suite at that point ran 163 tests,
with two live-MySQL skips. These measurements predate FORM expansion caching;
character-warm runs still executed FORM.

### Singlet projection without decomposing the complete product

For virtual characters `A = sum a_lambda chi_lambda` and
`B = sum b_mu chi_mu`, the invariant coefficient of `A*B` is
`sum_lambda a_lambda b_(lambda dual)`. Coefficients may be negative. Pseudoreal
irreps pair with themselves without an extra Frobenius-Schur sign.

- `_split_character_product` expands multiplicities into individual Adams
  factors and greedily balances two sides using `j * sum(labels)` as an
  approximate cost. It splits across repeated irreps before their entire
  products are decomposed. Individual Adams operations remain intact.
- `_character_singlets` plans required partial products, uses the existing
  persistent cache/process pool for products of one base irrep, and memoizes
  mixed partial decompositions in thread-local memory.
- `_singlet_pair` contracts the two partial decomposition dictionaries by
  looking up dual labels. `_dual_permutation` caches the Dynkin-diagram
  permutation obtained from the existing Sage duality helper.
- `_tensor_decompositions` is used only for intermediate mixed products.
  The complete product is never tensor-decomposed by the singlet path.
  Already-decomposed callers also use partial products and the same final
  pairing. Full decomposition calls still work when explicitly requested.

SQLite schema version 1 and product keys are unchanged. All 4,996 old singlet
entries from the benchmark databases were reused without recalculation.
The prototype in `/tmp/sci-singlet-projection/candidate` matched 319 singlet
queries against the existing algorithm; 202 also matched independent SU(2) and
SU(3) weight calculations. All 216 dual-label comparisons against Sage matched
across 18 Cartan types, covering every supported family. Seven benchmark index
cases matched exactly, including product groups and half hypers. Three-trial
cold projection medians were A8 t^18: 4.60 -> 0.849 seconds; A16 t^12:
1.47 -> 0.207 seconds; A32 t^8: 0.469 -> 0.117 seconds. The full A8 t^18 index
improved 6.21 -> 2.44 seconds; warm performance stayed about 1.70 seconds.

Details are in `output/benchmarks/singlet_projection_benchmark.md` and
`singlet_projection_timings.json`. The reusable benchmark accepts
`--baseline-ref a9b7009 --baseline-label existing --candidate-label sparse`.
At that earlier projection revision, the complete suite ran 170 tests in 11.461 seconds, with 168
passed and two live-MySQL skips. The seven new tests in
`test/test_singlet_projection.py` cover signed complex/pseudoreal pairings,
large integers, grouped Adams splitting, mixed products, independent SU(2)/SU(3)
weights, serial/parallel generation, trivial characters and zeros.

The split is a heuristic and does not eliminate all intermediate decomposition
costs. A16 t^18 projection finished in 1.92 seconds while the previous code
exceeded 25 seconds; no full old/new comparison was possible for that case.
All timings in this projection subsection predate FORM caching. The earlier
A32 t^18 probe exceeded its 25-second limit; the unlimited follow-up
on 10 September completed. It used SU(33) SQCD with 66 full fundamental hypers,
`processes=1`, `timeout=None`, and a fresh temporary SQLite file. Complete times
were 94.293 seconds cold and 1.786 seconds warm, with 117 exactly matching
Laurent monomials. Input validation/algebra initialization took 4.483 seconds,
FORM 1.357 seconds, FORM parsing 0.212 seconds, and projection 88.229 seconds.
One LiE request for `psi_3(adjoint)^2` took 82.699 seconds: 10 irrep terms in each
input and 91 in its output. All other LiE calls together took 5.066 seconds.
The warm run made no LiE calls. These are single wall-clock measurements with
one worker, not medians or default-process-count timings. See
`output/benchmarks/a32_unlimited/report.md`, `summary.json`, `events.jsonl` and
`results.json`; the exact index and profiling script are retained there too.
No production code was changed for profiling.

### Selecting already cached character factors

For a base irrep R, write `D_R(n) = product_j psi_j(R)^n_j`. If `a+b=n`
componentwise, `D_R(n) = D_R(a) tensor D_R(b)`. The current instance method
`_decomposition_dependencies(algebra, labels, powers)` keeps the usual
peel-one-factor split when both dependencies are already available, and for
products with at most two factors. Otherwise it queries lower-weighted-order
keys for that same algebra/irrep, includes thread-local entries and the known
`psi_1(R)=R`, and finds complementary cached pairs.

Available pairs are scored by the product of their numbers of irrep terms;
zero/scalar factors have zero estimated cost. Ties use total support size then
vectors. This is a heuristic, not a guarantee of the fastest LiE tensor call.
The batch dependency scheduler and actual calculation use the same choice.
The existing tensor helper handles signed coefficients, zeros and scalar
shortcuts. First Adams and all trivial-representation products return directly.
There is no schema/key migration or change in thread/process ownership.

The temporary candidate was tested before promotion. Five-trial targeted
persisted-cache misses improved 1.31-1.72x for the single-request API and
1.03-1.17x for the batch API. Each reduced two tensor calls to one; a
composite-only cache case also reduced one Adams call to zero. All 130
comparison products across A1, A2 fundamental/adjoint, C2 and G2 matched in
serial and three-worker runs; SU(2) also matched independent weights.

All 72 full-index comparisons at `t^18` matched across six theories and two
character-cache states (cold and filled through `t^12`), with FORM prefilled
for both implementations. They showed **no consistent overall speedup**;
tensor-call counts were identical. This change helps particular missing
products; it does not remove the expensive intermediate LiE tensor bottleneck.
Retained baseline/candidate files, report, raw timings and reproduction script:
`output/benchmarks/adams_cache_planner/`.

### Complete decomposition-cache builder and cutoff

```python
build_decomposition_cache(
    cartan_type, dynkin_labels, max_adams_order, *,
    cache_directory=None, database_path=None, processes=None,
    lie_executable="lie", timeout=600, progress=None,
) -> dict[int, int]
```

At each weighted order q, the builder enumerates all nonnegative integer
vectors `(n1,...,nq)` with `sum(j*n_j)=q` by calling
`common.math_utils.frobenius_solve(range(1, q+1), q)`. These are the integer
partitions in multiplicity notation; orders 1-6 have 1,2,3,5,7,11 solutions,
29 total. It writes one decomposition for every solution of the chosen irrep
into the existing character table. It does not fill singlet coefficients,
mixed-irrep partial products or the FORM cache.

Existing rows are skipped using keys alone, without decoding their payloads.
Missing solutions at an order are split among a lazily created persistent
`ProcessPoolExecutor` using `spawn`. Batches contain at most 64 requests,
with several batches per worker for load balance. Workers read lower-order
dependencies and calculate; the parent commits each completed batch. It
submits the next order only after all batches at the current order are saved.
Completed batches survive later errors/stops and are reused on rerun.
Concurrent independent builds can duplicate misses but preserve complete rows.

Every composite has two strictly lower-order factors, already cached at this
barrier. It therefore needs at most one tensor operation, with the usual
zero/scalar shortcuts. For a nontrivial irrep and an initially empty DB, one
uninterrupted build makes one logical Adams calculation at each j=2,...,M,
M-1 total. First Adams is known directly. This count excludes backend retries
and duplicate work by simultaneous independent builds.

The function returns `{order: total_product_count}`, including reused rows.
`progress(order, total, computed)` runs in the parent after each order is
committed. `processes=1` is serial; default is CPU count. Use a `__main__`
guard in multiprocess scripts. Maximum order and process count are positive
integers. Cache path options follow `CharacterDecompositionCache`; `timeout`
is per LiE invocation, with Python `None` meaning no subprocess timeout.
OR-Tools is imported lazily through `frobenius_solve` when building.

```bash
sage -python -m index.char_decomposition_cache A2 \
  --dynkin-labels 1 0 --max-adams-order 9 --processes 4 \
  --cache-database /tmp/index-demo/char_decomposition_cache.db
```

The direct script form also works. `--max-order` aliases `--max-adams-order`;
other flags include `--cache-directory`, `--lie-executable` and numeric
`--timeout`. The CLI reports computed/reused counts by order, then the total
and DB path. Reported failures exit with status 2. A warm rerun enumerates and
checks keys, but performs no LiE work and starts no worker pool.

For an index through `t^N`, letters start at `t^2`, so an Adams factor j costs
at least `t^(2j)`. A same-irrep product of weighted order q therefore has
`t` degree at least 2q, giving **q <= floor(N/2)**. Use that maximum separately
for every required adjoint and matter irrep, including distinct conjugates.
For A2 SQCD at `t^18`, prebuild labels `(1,0)`, `(0,1)` and `(1,1)` through
order 9. Product groups obey the bound per factor/irrep, not after summing
duplicated character orders across bifundamental gauge factors. At N<2 no
prebuild is needed. Exhaustive prebuilding is optional and can be substantially
more expensive than filling only the projector's requests.

Twelve builder regressions test completeness against independent SU(2) weights,
A1/A2/C2/G2 serial/parallel equality, pool reuse, warm skips, resume/failure
persistence and both CLI entry points. Real worker logs through order six
show Adams(2),...,Adams(6) exactly once each and 23 tensor calls across multiple
worker PIDs. No broad wall-time speedup for exhaustive prebuilding is claimed.

### Sage result ring and serialization

`_to_sage_polynomial` returns one flat Laurent polynomial in

```text
QQ[t^+-1, y^+-1, u^+-1].
```

The ring formally permits negative powers of `t`, but calculated indices only
contain nonnegative `t` powers. Because the ring is flat, converting an index
to `str` gives a sum of monomials without grouping coefficients with common
`t` powers, for example:

```text
t^4*u^4 + 36*t^4*u^-2 - t^5*y*u^2
```

The public function `parse_index_polynomial(text)` converts this stored string
back into the canonical Sage Laurent-polynomial ring. It preserves exact
rational coefficients and validates the allowed polynomial grammar before
using Sage evaluation.

Main APIs:

```python
calculate_index(data, order, ...)
calculate_index_internal(factors, hypermultiplets, order, ...)
calculate_index_from_file(path, order, ...)
parse_index_polynomial(text)
```

CLI example:

```bash
sage -python index/n2_theory_index.py \
  anomalies/example_a1.json \
  --order 18 \
  --char-cache-database char_decomposition_cache.db \
  --form-cache-database form_expansion_cache.db
```

## Coulomb-branch implementation

Files:

- `index/n2_theory_coulomb_branches.py`
- `test/test_n2_theory_coulomb_branches.py`

These replaced `index/n2_theory_branches.py` and its corresponding test file.
Use the new import path. The spectrum extraction function is
`extract_coulomb_branch_spectrum_from_index`, and the module's introductory
docstring now uses that current callable name.

Public APIs (all in the Coulomb module):

```python
calculate_coulomb_branch_index(spectrum=None, max_dimension=None,
                              *, full_index=None, ...)
calculate_coulomb_branch_index_from_full_index(full_index,
                                             *, max_dimension=None)
calculate_lagrangian_coulomb_branch_index(gauge_factors, max_dimension, ...)
coulomb_branch_spectrum_from_gauge_factors(gauge_factors)
calculate_plethystic_exponential(plethystic_log, max_dimension, ...)
calculate_plethystic_logarithm(coulomb_branch_index, max_dimension, ...)
extract_coulomb_branch_spectrum_from_index(coulomb_branch_index,
                                         max_dimension, ...)
parse_coulomb_branch_index(text)
```

`gauge_factors` accepts a `GaugeFactorData` or an iterable of them. Lagrangian
generator dimensions are the degrees of the basic Weyl-invariant polynomials,
obtained using `WeylGroup(cartan_type).degrees()`. Their sorted multiset includes
repetitions within a factor (e.g. `D4`: 2, 4, 4, 6) and across product factors.
The direct gauge-spectrum API returns integers; the property helper returns
`Fraction` objects. Matter is unnecessary for this Coulomb calculation.

For generator dimensions Delta_i:

```text
I_C(x) = PE[sum_i x^Delta_i] = product_i (1 - x^Delta_i)^(-1).
```

Spectrum input may be an iterable or dimension-to-multiplicity mapping.
The lower-level PE accepts signed integer coefficients for future
generator/relation data. FORM performs the truncated expansion and rational
arithmetic. Rational dimensions are rescaled with an LCM, using
`q = x^(1/L)` so `x^Delta = q^(L*Delta)`. Results are converted to
`COULOMB_INDEX_RING = PuiseuxSeriesRing(QQ, "x")`.

The full-index Coulomb limit in project conventions holds `x = t^2*u^2`
fixed. A monomial `t^a*y^b*u^c` survives if `a == c` and maps to `x^(c/2)`.
The code rejects divergent terms (`a < c`), surviving `y` dependence, and
negative dimensions. Both a Sage full-index polynomial and its string are
accepted.

The inverse calculation uses

```text
PL[I](x) = sum_(k>=1) mu(k)/k * log I(x^k).
```

FORM evaluates the ordinary logarithm by expanding `log(1 + delta)`, where
`delta = I - 1`. Python applies the exact Mobius coefficient transform:

```text
log I(q) = sum_n b_n q^n
[q^m] PL[I](q) = sum_(k divides m) mu(k)/k * b_(m/k).
```

The existing FORM runner, output parser, and conversion to the Sage ring are
reused. `calculate_plethystic_logarithm` returns signed rational coefficients.
`extract_coulomb_branch_spectrum_from_index` requires nonnegative integral PL
coefficients and returns repeated `Fraction` dimensions; negative coefficients
or nonintegral multiplicities raise `ValueError`.

Input requires constant coefficient 1 and no negative powers. A finite-precision
Sage series must be known through the requested inclusive cutoff. Series strings
emitted by the project are supported, including fractional exponents. Generated
truncations are polynomial-like outputs without an `O(...)` precision marker:
callers must retain the original cutoff and must not request coefficients beyond
it. A nonnegative PL through a finite cutoff does not establish global freeness.

For example, the truncation of `PE[x^2]` through degree two is `1 + x^2` and
reports infinite precision as a Sage object. Incorrectly requesting its PL
through degree four produces `x^2 - x^4`; that negative term is an artifact of
exceeding the known cutoff, not a relation in the original Coulomb ring.

Verified examples from the session:

- `PE[x^2 + x^3]` through dimension 8 gives PL `x^2 + x^3` and spectrum `(2, 3)`.
- Dimension `6/5` survives the PE/PL round trip, including serialized input.
- Repeated dimensions retain their multiplicity.
- `PE[2*x^2 - x^4]` returns signed PL `2*x^2 - x^4`; the free-spectrum API rejects it.

## GUI anomaly workflow (14 September 2026)

Sources: `gui/n2_db.ui`, `gui/settings.ui`, `gui/n2_db.py`,
`gui/theory_builder.py`, `gui/candidate_workers.py`, and `gui/README.md`.
PyQt6 loads the editable forms directly; no UI code-generation step is needed.
The anomaly tab has editable multiline input and **Load theories...** on the
left, and the cache checkbox, read-only log and **Build**/**Stop** on the right.

### Input and persistent settings

One nonempty line requests a gauge-group enumeration; commas separate product
factors, for example `A1, C2`. Inputs use supported Cartan types. Blank factors
or unsupported types are logged with line numbers; other usable lines continue.
Load accepts multiple UTF-8 files, including BOM and Windows line endings,
appends them in selection order with line boundaries, and preserves existing
text. The whole load is one undoable edit. Any read/decode failure rejects the
entire load with its filename and reason. Cancel and empty files do nothing.

New Preferences fields match backend defaults and persist through QSettings:

| Saved key | Default | Contract |
| --- | --- | --- |
| `index/full_max_order` | `18` | Inclusive nonnegative integer full-index order |
| `index/coulomb_max_dimension` | `90` | Inclusive exact nonnegative rational; reduced string, e.g. `6/5` |
| `tools/processes` | `-1` | `os.cpu_count() or 1`; positive values limit workers, `1` is serial, `0` invalid |

Older settings files receive these defaults. OK saves; Cancel discards edits.
Existing native keyring password storage and configuration-path compatibility
remain in place. Build uses a snapshot of saved settings and input. The portable
launch command, with the user's Sage environment active and `sage` on PATH, is:

```bash
sage -python -m pip install -r gui/requirements.txt
sage -python -B gui/n2_db.py
```

`-B` is optional: it suppresses Python bytecode writes, not mathematical
calculations or SQLite cache use. The GUI shell can run in ordinary Python,
but Build locates Sage beside that interpreter or on PATH and needs the backend
dependencies. Cache preparation also requires LiE.

### Build, parallel ownership and cache bounds

The GUI starts `gui.theory_builder` in a separate Sage process and sends
credentials/settings through stdin. The coordinator validates the gauge lines,
initializes the MySQL schema once, and enumerates each group serially using
`enumerate_simple_theory_candidates` or `enumerate_product_theory_candidates`
from `common.n2_theory_iter`, with their existing defaults. Candidate lists are
materialized; free all-singlet matter is omitted by the enumerator.

For each group, `gui.candidate_workers.process_candidates` starts at most
`min(resolved CPU count, candidate count)` spawned workers. It sends one
candidate per task, keeping at most twice the worker count submitted/running.
Workers receive new tasks dynamically. A group's pool finishes before the next
group starts. One worker uses the serial path.

`calculate_n2_theory_properties` checks each candidate. Invalid theories log
their reason; unexpected checking failures count as `check_failed`. Valid
theories are revalidated and stored by `store_lagrangian_theory`; only basic
properties are inserted. Each spawned worker owns/reuses its own MySQL
connection and Sage state, closes its connection on normal shutdown, and uses
`initialize_schema=False`. No connection/cursor is shared across threads or
processes. A connection that failed an import is closed before later tasks.
Only the coordinator combines results, counters, representations and logs.

The log reports each group, its candidate count, and per-group/overall
`valid`, `invalid`, `added`, `existing`, `check_failed` and `db_failed` totals.
A valid theory with a failed insert remains valid and increments `db_failed`.
Repeated input lines normally become existing records on later occurrences.
Failures include exception type and reason; later work continues when possible.

If cache building is enabled, collect all distinct `(Cartan type, Dynkin labels)`
pairs from valid theories across all groups, including existing records and
valid theories whose insertion failed. Include vector adjoints, factor singlets,
matter orientations and full-hyper conjugates. After the candidate pools finish,
call `index.char_decomposition_cache.build_decomposition_cache` for each pair
through weighted Adams order `index/full_max_order // 2` (default 9). A zero
bound skips prebuilding. Use the saved character-cache filename, LiE executable,
invocation timeout and CPU limit. Reuse existing rows and report computed/reused
products per order. The saved Coulomb cutoff, FORM executable and FORM-cache
file are not consumed by this action because no indices are calculated.

Stop halts submissions, cancels queued work where possible and signals workers
before further checks/inserts. Active calls finish and their results are drained
before totals are reported, preserving committed-insert counts. Enumeration must
finish first; cache generation stops after the current order commits. Closing
the window requests the same stop. A broken process pool is reported with
potentially unknown database outcomes; it does not silently replay writes.

### File and GUI log contract

Every Build attempt creates a UTF-8 file, including attempts that fail before
backend startup: `logs/log_anomalies_YYYYMMDD_HHMMSS_ffffff.log` under the
project root. Local-time microseconds and exclusive creation avoid overwrites.
The GUI shows the full path. Both destinations use
`[YYYY-MM-DD HH:MM:SS.mmm+HH:MM] [LEVEL] message`, with the actual local UTC
offset (positive or negative). Every line of a multiline message gets the same
prefix. The GUI preserves timestamps supplied by the build process; candidate
pool results receive timestamps when the coordinator emits them.

Progress uses INFO, rejected theories use WARNING, and failures use ERROR.
Unstructured stdout uses INFO and stderr WARNING; nonzero exits/crashes are
also reported as ERROR. Passwords are redacted before output. Logs flush on
arrival and close on completion, failure or Stop. File errors appear in the
GUI and on-screen logging continues. Generated `logs/` output is ignored by Git.

### Spin naming scope

The existing `anomalies/lie_algebra.py` convention displays B and D algebras
as `Spin(2r+1)` and `Spin(2r)`. This session did not change that convention or
migrate stored group names. The input/model does not separately encode a global
quotient, line-operator spectrum or discrete theta data; renaming Spin to SO
would not implement those choices.

## MySQL database

File: `common/n2_theory_db.py`

The database uses the default PyMySQL client. The current production schema version is 1.
The tables are:

- `schema_metadata`
- `theories`
- `theory_properties`
- `lagrangian_realizations`
- `non_lagrangian_realizations`
- `gauge_factors`
- `hypermultiplets`
- `hypermultiplet_representations`
- `flavor_symmetry_factors`
- `exactly_marginal_couplings`

The `non_lagrangian_realizations` table is currently a placeholder for future
work.

Central charges are stored exactly as numerator/denominator JSON. Stored,
indexed generated decimal columns permit efficient numerical range queries.
The full index is stored as a JSON string in `superconformal_index_json`, the
Coulomb index as a JSON string in `coulomb_branch_index_json`, and the spectrum
as an array of exact fractions in `coulomb_branch_spectrum_json`. All three also
appear under their corresponding keys in the combined `properties_json`.

The production schema directly includes `superconformal_index_order BIGINT
UNSIGNED NULL` and `coulomb_branch_index_max_dimension_json JSON NULL` for
calculation cutoffs, plus `disconnected_sector_count` and
`disconnected_sectors_json` described above. The shared flavor table stores
`half_hyper_units`; the full/half split remains in each realization's
`hypermultiplets` rows and input/anomaly JSON. Initialization creates this
layout and validates version 1; no schema upgrades or data backfills are run.

New imports put SQL NULL in all three index/spectrum columns and both cutoff
columns; their combined JSON initially contains only basic properties.
`_insert_shared_properties` compares only basic physical properties, normalizes
obsolete full/half flavor metadata, and preserves existing index data when
attaching another compatible realization. Different central charges or matter
multiplicities still reject the attachment.

`update_lagrangian_indices(connection, realization_id, indices)` accepts the
separate calculator's dictionary or a partial result with the matching cutoff.
It locks the shared row and updates the dedicated columns and combined JSON
atomically. Full and Coulomb cutoffs are compared independently. Missing data
is filled; a known strictly higher cutoff replaces its index; equal or lower
cutoffs keep the stored value. None/omitted fields do not erase data. Spectra
are complete rather than truncated: missing spectra are filled, identical
spectra are retained, and conflicting spectra reject the transaction.

Legacy indices with no recorded precision are retained. When their original
requested cutoffs are known, record them explicitly before upgrading:

```python
record_lagrangian_index_cutoffs(connection, realization_id, order=18, max_dimension=90)
```

Only use values verified from the original calculation. This API fills unknown
cutoffs; it cannot change an already known cutoff or annotate a missing index.
The highest nonzero monomial is not a reliable precision estimate.

### Separate index worker

`common/n2_theory_db_indices.py` streams bounded pages of stored Lagrangian
inputs, visiting one realization per shared theory. Its default mode fills any
missing full index, Coulomb index, or spectrum. `--upgrade` additionally
calculates components with known lower cutoffs. Complete legacy records with
unknown precision are reported without recalculating their indices.

Calculations happen outside database transactions. Each successful component
is saved independently, so partial failures retain completed work. Re-running
resumes from remaining missing/lower-order components. Competing workers may
duplicate a calculation, but row locking prevents a lower/equal-order result
from replacing one committed by another worker. This is not a job-claim queue.

```bash
# Run from the project root using Sage's Python; password uses N2_DB_PASSWORD.
sage -python common/n2_theory_db_indices.py database_name --user database_user
sage -python common/n2_theory_db_indices.py database_name --user database_user \
  --upgrade --index-order 24 --coulomb-max-dimension 100 --limit 10
```

The worker accepts host/port/socket options, `--char-cache-database`, executable
paths, `--timeout`, and `--processes`. It emits JSON outcomes and a summary;
exit 0 means no component errors, 1 means component failures, and 2 means a
configuration/database-level error. `fill_lagrangian_indices` exposes the same
workflow as a generator. API connections must have an initialized schema and
no caller-owned transaction before starting index updates.

### Duplicate behavior

`store_lagrangian_theory` is storage-idempotent for the same normalized
Lagrangian realization:

- It returns the existing theory and realization IDs.
- `StoredTheory.inserted` is `False`.
- The duplicate return path does not insert or update theory/realization data;
  schema initialization still occurs before this lookup.
- A newly supplied display name is ignored.
- The existing `updated_at` value is not changed.

The canonical realization payload ignores hypermultiplet order, combines
duplicate hypermultiplets, omits zero multiplicities, and identifies a complex
representation with its conjugate. Product gauge-factor order is still
significant.

Canonicalization now selects the larger label tuple, favoring fundamental
representations, consistently with enumeration and flavor metadata. This
changes hashes and shared flavor labels for complex representations compared
with the previous smaller-tuple convention. Existing database rows have not
been migrated; their old hashes/properties need a deliberate migration before
relying on deduplication across the convention change.

**Full/half normalization is fixed for new imports:** for each pseudoreal
representation, hashing first sums `2 * full + half`, then uses full pairs and
one remaining half if needed. Product groups use the reality of the complete
tensor-product representation. Real and complex representations remain full
hypers. SU(2) with four full fundamentals, eight half fundamentals, or a mixed
description now has one hash and returns the same theory/realization IDs.
Tests cover both insertion orders and attaching with an explicit `theory_id`.
For C3, odd half-hyper multiplicities in distinct irreps remain separate.

`_shared_properties` removes `full_hypermultiplets` and `half_hypermultiplets`
from a copy of flavor metadata. The public property calculator still reports
the original split. Equivalent shared properties compare equal, including
legacy JSON that retains the split. Actual FORM/LiE calculations confirm equal
superconformal indices through `t^6` and Coulomb indices through order 12 for
full/half SU(2) fundamentals and SU(2)^3 trifundamentals. Database behavior and
schema migration were tested with recording/mocked connections; no live
database was modified.

The new full/half normalization preserves hashes for descriptions already
written entirely as full hypers under the current conjugacy convention.
Previously stored unpaired half-hyper hashes and duplicate theory rows still
need a separate data migration. Shared flavor metadata additionally contains
gauge representations and factor IDs, which must be considered when supporting
different realizations of one theory.

Anomaly checking and basic property calculation happen before the duplicate
lookup. Importing an existing realization incurs no index or spectrum work. The database path also performs
anomaly parsing twice: once in `_checked_results` and again in
`calculate_n2_theory_properties`.

Different dual Lagrangian descriptions are not recognized automatically. Use
`theory_id` or `--theory-id` to attach a different realization to an existing
theory. Its basic shared properties must match the existing record; index
availability or precision does not affect that comparison. A physical mismatch
rolls back the insertion.

New data insertion uses one explicit InnoDB transaction. A failure rolls back
the theory-related DML, although schema initialization occurs before that
transaction. Workers disable schema initialization after the
coordinator finishes it; they never run concurrent DDL for a build.

### Parallel insertion and deadlock repair (14 September 2026)

Concurrent equivalent imports use unique-key conflict handling: MySQL error
1062 rolls back the losing transaction, then looks up and returns the committed
winner as `inserted=False`. The duplicate path preserves existing names, indices
and timestamps. A failed physical-property comparison still rolls back.

Previously `_insert_shared_properties` always ran a missing-row
`SELECT ... FOR UPDATE` before a fresh properties insert. Under REPEATABLE READ,
distinct new IDs could share a primary-index gap, hold compatible gap locks and
then block one another's INSERTs. The isolated MySQL deadlock report confirmed
that cycle in `theory_properties`. The importer now passes `new_theory=True`
only when it inserted a fresh parent in the same transaction, skipping that
unnecessary lookup. Existing-theory attachments retain the locking read for
comparison/enrichment. No schema or isolation-level change is required. See
[MySQL InnoDB locking](https://dev.mysql.com/doc/refman/8.0/en/innodb-locking.html)
for gap and insert-intention lock semantics.

Deadlock 1213 and lock timeout 1205 still trigger explicit rollback and at most
two retries of the whole transaction (three attempts total), waiting 50 ms then
100 ms. After exhaustion, the error is logged and `db_failed` increments;
other candidates continue. Other failures, including uncertain commit/connection
outcomes, are not automatically replayed. Rollback failures include the original
error. A later Build can retry missing theories and recognize committed records.

The regression fixture uses A1 with four full fundamentals plus 1--256 free full
singlets, producing distinct valid inputs for direct checking/storage tests.
These free singlets are not produced by normal enumeration. Eight workers
previously left 93 of the 256 inserts failed after retries; the fixed run inserted
all 256. The synchronized two-worker regression previously required one rollback
and now needs none. The two fixed runs added zero server deadlocks. This is a
controlled local result, not a guarantee against every possible deadlock.

CLI example:

```bash
export N2_DB_PASSWORD='...'

sage -python common/n2_theory_db.py \
  anomalies/example_e6.json \
  database_name \
  --user database_user \
  --name "E6 theory"
```

## Tests

### Current validation and harness fixes (14 September 2026)

The final backend run after the missing-properties-lock fix reported:

```text
Ran 268 tests in 33.518s
OK
```

All live MySQL tests ran against a password-protected disposable MySQL 8.0.46
instance, using actual Sage/FORM/LiE. The earlier focused run passed 37 tests,
including the synchronized pair, 256-candidate parallel stress fixture and
database unit regressions. During documentation refresh, the isolated offscreen
GUI suite passed all 32 tests in 0.869 seconds. These results supersede the
older totals below; timings are local observations, not performance guarantees.

`test/test_n2_theory_db_indices.py` now supplies the configured test password
and Unix socket through `N2_DB_PASSWORD` and `N2_DB_UNIX_SOCKET` when invoking
the CLI entry point, restores the environment afterward and reports stderr on
failure. `test/test_gui_candidate_workers.py` records test-owned MySQL connection
IDs, including spawned workers, and waits up to five seconds for their closure.
It ignores unrelated clients and includes a negative check for an owned leak.
The new database regressions cover direct fresh-properties insertion, retained
attachment locking, a synchronized concurrent pair and 256 distinct candidates.

Run with Sage and explicit disposable `N2_TEST_MYSQL_*` connection values:

```bash
PYTHONDONTWRITEBYTECODE=1 sage -python -B \
  -m unittest discover -s test -p 'test_*.py' -v
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 sage -python -B \
  -m unittest test.test_gui_n2_db test.test_gui_index_tab \
  test.test_gui_logging_utils -v
```

Live tests reset the configured test tables; never point them at production.
Omit `N2_TEST_MYSQL_DATABASE` to skip live integrations. `all_test.sh` still
contains local connection settings; these repairs changed the tests, not that
script. The GUI tests use temporary settings and a credential test double.

### Retained backend coverage and historical snapshots

The suite includes anomaly, property, index, Coulomb, database, enumeration,
Lie-algebra and math-utility tests, plus dedicated modules:

- `test/test_character_decomposition_cache.py`
- `test/test_singlet_projection.py`
- `test/test_form_expansion_cache.py` (20 FORM-cache regressions)
- `test/test_cached_decomposition_planning.py` (7 planner regressions)
- `test/test_build_decomposition_cache.py` (12 builder regressions)

The documentation-time verification on 2026-09-10 used actual Sage, FORM and
LiE and reported:

```text
Ran 209 tests in 19.658s
OK (skipped=2)
```

That means **207 passed and 2 skipped**. The two skips require live MySQL;
schema migration and inserts use recording/mock tests without a configured
test server. Run the same suite without generating bytecode or contacting a
live MySQL test database:

```bash
N2_TEST_MYSQL_DATABASE= DOT_SAGE=/tmp/codex-sage-cache \
  PYTHONDONTWRITEBYTECODE=1 \
  /home/subo-lee/miniconda3/envs/sage/bin/sage -python -B \
  -m unittest discover -s test -p 'test_*.py'
```

Older 150-, 170-, 190- and 197-test snapshots precede later additions and must
not be reported as current totals. Benchmark reports retain their original
validation counts as historical evidence.

Coverage includes irrep bounds and conjugates, product spectator factors,
simple-theory enumeration against an exhaustive rational reference, and all
six expected A32 contents. The direct Tits/Sage comparison covers 146 irreps
across 23 Cartan types, including every supported family, singlets, adjoints,
and real/complex/pseudoreal examples. All comparisons matched. Existing tests
also cover Coulomb PE/PL, fractional dimensions, precision validation, property
serialization, schema version 6, full/half deduplication, and the shared-property
backfill helper.
Canonicalization tests cover simple A/D/E complex conjugate pairs and product
bifundamentals, verifying that simultaneous conjugates share hashes and flavor
labels while `(3, 3)` and `(3, 3bar)` remain distinct.

The property and database unit tests mock the expensive index calculation.
Index tests use temporary cache directories and delete them afterward. Thus,
the test suite does not normally populate the project-root cache.

Live MySQL tests require environment variables such as:

```bash
export N2_TEST_MYSQL_DATABASE='some_test_database'
export N2_TEST_MYSQL_USER='...'
export N2_TEST_MYSQL_PASSWORD='...'
```

The test database name must contain `test`.

## Documentation

### PDF package scope correction (14 September 2026)

At the user's request, the index guide's former chapter 12 (property calculation
and staged database updates) and chapter 13 (anomaly GUI and concurrent imports)
are removed. Their shared sources remain included by the implementation
reference only. The removed chapter 12 overview already appears in the
reference's basic/index-property APIs and Coulomb-precision explanation; its
direct-index versus SCFT-wrapper boundary is now explicit in the property API
section. No duplicate chapter was added to the reference.

The index guide's contents and opening scope are updated, and Interpretation
and limitations becomes chapter 12. Both retain their existing index equations,
cache descriptions and dated benchmarks. Build/source dependencies are recorded
in `output/pdf/BUILD.md`. No application code or tests changed in this edit.

Current outputs are the **44-page implementation reference** and **20-page
index guide**. Both were rebuilt and visually checked. The guide's former
chapters occupied nine pages; its retained index chapters, mathematical
equations and embedded pages are preserved. The reference gained the API
boundary clarification in its existing property section, rather than a second
copy of either chapter. Older page counts below are historical snapshots.

### GUI and concurrency PDF revision (14 September 2026)

The initial revision included in both canonical PDFs the shared source
`output/pdf/gui_anomaly_workflow.tex`. It documents the complete anomaly tab,
saved cutoffs and CPU count, portable launch, file loading, candidate scheduling,
connection ownership, cache representation union/bound, cooperative Stop,
timestamped file/GUI logs, test-harness repairs and missing-row deadlock fix.
It records the 268-test backend run, fresh 32-test GUI run and controlled
before/after stress evidence, while retaining older dated measurements.

`output/pdf/n2_implementation_reference_summary.pdf` is the package reference;
`index/n2_theory_index_Mathematical_Background.pdf` is the index guide. Their
existing mathematical equation blocks and the embedded original guide pages
are retained. `output/pdf/BUILD.md` records source inputs, rebuild commands and
the final page/layout verification. This refresh changes documentation only;
application changes from earlier in the session are preserved.

That initial revision had a **44-page reference** and **29-page index guide**. All
pages were rendered and visually reviewed, including reading-size checks of
the new sections. Builds have no unresolved references/citations or overfull
boxes. All 48 reference and 15 guide equation/align blocks match their previous
sources; embedded guide pages 2-4 have identical extracted text. The new shared
GUI chapter was reference section 5 and guide section 13; the later scope
correction above removes it from the guide.

### Property and database PDF revision (12 September 2026)

That revision added the separate basic/index-property APIs to both PDFs,
schema 7, the deferred worker, independent cutoff comparisons, unknown legacy
precision, atomic writes, and component-level retries. They include worker CLI
examples and the previously recorded 238-test result. This PDF edit did not
rerun those tests or benchmarks.

That revision's implementation reference had **39 pages**; the index guide had
**24 pages**. Every page was rendered and visually reviewed, with changed pages
also checked at reading size. There are no unresolved references/citations or
overfull boxes. All 48 reference and 15 guide authored equation/align blocks
remain verbatim; original guide pages 2-4 retain their embedded text. The new
`output/pdf/property_database_workflow.tex` is a shared source required by both
builds. `output/pdf/BUILD.md` records the current sources and verification.

### Retained documentation history

- `anomalies/README.md`
- `index/n2_theory_index_Mathematical_Background.pdf`
- `output/pdf/n2_implementation_reference_summary.pdf`
- `output/pdf/n2_implementation_reference_summary.tex`
- `output/pdf/n2_theory_index_Mathematical_Background.tex`
- `output/pdf/cache_session_algorithms.tex`
- `output/pdf/cache_session_validation.tex`
- `output/pdf/property_database_workflow.tex`
- `output/pdf/source_assets/n2_background_preserved_pages_2_to_4.pdf`
- `output/pdf/BUILD.md`

The PDF files contain the mathematical background, implementation equations,
references, and explanations of the FORM and LiE code.

The implementation reference PDF and its LaTeX source correctly state that
`central_charges` contains exact `a,c` only. The 2026-09-09 database update
expands sections 4.4-4.6 to cover schema version 6: all ten tables, nine foreign-key
relationships, primary/unique keys, shared versus realization ownership,
full/half normalization, and migration/backfill limitations. It also records the
134-test validation result as a dated historical snapshot.

The earlier 10 September update documents the committed FORM expansion cache,
exact raw-program keys and serialization, concurrent access, cross-theory hits,
weighted-order cached-factor planning, the complete builder API/CLI, order
barriers, resume semantics and the `floor(N/2)` cutoff proof. Controlled FORM
benchmarks, the planner's limited full-index gains, builder operation-count
checks and the 209-test snapshot are included in both PDFs. Older sparse
projection and A32 timings are explicitly labeled as predating FORM caching.

The two new shared LaTeX inputs keep these descriptions and measured tables
consistent between the standalone PDFs. The index guide also embeds original
pages 2-4 through `source_assets/n2_background_preserved_pages_2_to_4.pdf`;
that existing asset is unchanged in this turn. Keep it and both shared inputs
with the TeX sources. New equations use unnumbered displays so existing
mathematical equation numbers remain stable. Unrelated anomaly, Coulomb,
Higgs-design and MySQL sections retain their explicitly dated content.

`output/pdf/BUILD.md` records reproducible build and visual-review commands.
At that earlier revision, both outputs were rebuilt and checked: 34 pages for the
implementation reference and 20 for the index guide. Final logs have no
unresolved citations/references or overfull boxes. Original numbered equations
remain unchanged; 23 original reference pages retain identical body text, and
index-guide pages 2-4 retain identical text from the preserved asset. No application source, production cache, live
MySQL database, git staging or existing bytecode is intentionally modified.

Claude's supplied audit used a different sandbox and a Sage replacement for
LiE. The current review reproduced its substantive findings, the FORM wildcard
substitution behavior, and the full/half and Casimir examples. Its independent
reference scripts, randomized cases, and 87-entry cache snapshot were not
provided, so those particular historical experiments were not certified by
this review. Agreement on tested cases is not a proof for arbitrary inputs.

The user also provided a FORM manual at:

```text
/home/subo-lee/문서/수업/2026년 1학기/form-5.0.0-manual.pdf
```

## Hall-Littlewood and Higgs-branch discussion (not implemented)

### States, primaries, and grading

Using the conventions of Gadde et al., arXiv:1110.3740, the index condition is
`E - 2*j2 - 2*R + r = 0`. The HL limit additionally imposes
`E +/- 2*j1 - 2*R - r = 0`, giving

```text
HL contributing states: E = 2R + r, j1 = 0, j2 = r.
Higgs-ring primaries:    E = 2R,     j1 = j2 = 0, r = 0.
```

Higgs primaries are scalar bottom components of `B-hat_R` multiplets. The HL
index can also receive contributions from other states, including descendants.
In an index trace, spin symbols denote Cartan eigenvalues; a claim that a state
is scalar requires the Lorentz representation to be trivial.

The paper's HL weight is `(-1)^F * tau^(2*(E-R))`. For Higgs primaries this is
`tau^E`; it is not an energy grading for every possible HL state. The paper uses
`t_paper = tau^2`, which is different from this project's `t`.

### Vector letter and F-term constraints

For full hypers and adjoint chiral field `Phi`, at zero masses and FI parameters:

```text
W = sqrt(2) sum_i Qtilde_i Phi Q_i
  = sqrt(2) sum_a Phi^a mu_C^a,
mu_C^a = sum_i Qtilde_i T^a Q_i.
```

On the Higgs branch set `Phi = 0`. The remaining F-terms are `mu_C^a = 0`,
quadratic moment-map equations in the gauge adjoint. Scalars have HL weight
`tau`; the equations have weight `tau^2 * chi_adj(z)`.

For the polynomial ring `S` of hyper scalars, quotienting by a regular homogeneous
equation of degree d and weight w multiplies its Hilbert series by
`1 - tau^d*w`. If the moment-map components form a regular sequence, their
combined factor is

```text
product_(adjoint weights w) (1 - tau^2*w) = PE[-tau^2*chi_adj(z)].
```

This matches the HL vector letter. The surviving vector fermion has
`(E,R,r,j1,j2) = (3/2,1/2,1/2,0,1/2)` and contributes `-tau^2` per adjoint
weight. For SU(2), the constraint factor is

```text
(1 - tau^2*a^2)(1 - tau^2)(1 - tau^2/a^2)
= 1 - tau^2*chi_3(a) + tau^4*chi_3(a) - tau^6.
```

The Koszul complex with formal odd variables `eta_a` and `d eta_a = mu_C^a`
has degree-zero homology `S/(mu_C)`. Its alternating character is the vector
factor times the free scalar series. Regularity makes higher Koszul homology
vanish. This condition concerns the equations before gauge projection; it does
not require the final ring of gauge invariants to be a complete intersection.

The HL integral is

```text
I_HL(tau,a) = integral_G dmu(z) PE[
    tau*chi_V(z,a) - tau^2*sum_gauge_factors chi_adj(z)
].
```

Here V contains every holomorphic hyper scalar, including both halves of each
full hyper. Gauge projection selects gauge invariants; it must not project
flavor factors. The formula equals the Higgs Hilbert series under suitable
conditions, including regularity as a sufficient condition in this setup.
Do not assume equality for every SCFT or for all genus-zero constructions.
Extra HL sectors can survive when the moment-map complex has higher homology.

### HL limit in the current project's variables

The existing full-index kernels are

```text
J = 1 / ((1 - t^3*y)*(1 - t^3/y))
K_hyp = J*(t^2/u - t^4*u)
K_vec = J*(t^2*u^2 - t^4/u^2 - t^3*y - t^3/y + 2*t^6).
```

Hold `tau = t^2/u` fixed and take `t -> 0`, with `u = t^2/tau` and fixed y.
Then `K_hyp -> tau` and `K_vec -> -tau^2`.

For an already computed full index:

```text
t^a*y^b*u^c -> t^(a+2c)*y^b*tau^(-c).
```

Terms with `a+2c=0` survive; terms with positive `a+2c` vanish; negative
`a+2c` indicates a divergent term. The surviving expression must be independent
of y. An input known through `t^N` can determine integer HL powers through
`tau^floor(N/2)`, provided it contains all coefficients through that order.

A direct HL calculation would reuse the FORM character expansion and LiE gauge
projection, replace kernels with `tau` and `-tau^2`, and truncate in tau.
The full-index builder's minimum-letter-degree assumptions (`order // 2`)
cannot be copied unchanged: an HL hyper letter has degree 1. This direct
approach avoids computing full-index terms that disappear in the limit.

### Unrefined versus flavor-refined Higgs data

For actual Higgs-ring operators:

```text
H(tau,a) = sum_(Delta,lambda) m_(Delta,lambda)*chi_lambda^F(a)*tau^Delta
H(tau,1) = sum_Delta N_Delta*tau^Delta,
N_Delta = sum_lambda m_(Delta,lambda)*dim(lambda).
```

Setting flavor fugacities to 1 preserves total operator counts, including
composites, and their dimensions. It does not select flavor singlets and it does
not change gauge projection. It loses flavor representation labels, which
generally cannot be reconstructed from the counts. For example, the SU(3),
six-flavor test's `36*t^4/u^2` becomes `36*tau^2`; refinement distinguishes
`(chi_35^SU(6) + 1)*tau^2`.

"Higgs spectrum" needs interpretation: coefficients of H count independent
ring operators, while primitive generators require further ring/PL analysis.
The signed PL includes relations and can include higher syzygies. Positive PL
terms are not universally new generators. A Hilbert series alone need not
uniquely determine a minimal ring presentation, even with flavor refinement.
The free Coulomb-spectrum extraction function should not be used as a general
Higgs-generator extractor. Its lower-level numerical PL machinery is reusable.

The advised minimal next step, if requested, is unrefined HL/Higgs calculation
with explicit treatment of when HL equals the Hilbert series. Flavor refinement
is optional if total dimension-by-dimension counts meet the user's needs.

Flavor refinement would require the following changes:

1. Reuse/extract flavor-block grouping from `common/n2_theory_properties.py`
   into a shared module to avoid circular imports (properties already imports
   index). Construct gauge-flavor tensor-product scalar representations.
2. Extend `CharacterSpec`/`_character_basis` to identify gauge versus flavor
   factors. For n full complex hypers replace numerical multiplicities with
   `chi_R^G*chi_n^U(n) + chi_Rbar^G*chi_nbar^U(n)`. For n real full hypers use
   the fundamental `2n` of Sp(n); for m pseudoreal half-hyper units use the
   vector m of SO(m). Track abelian flavor charges explicitly. The project
   variable u is an R-symmetry fugacity, not such a flavor variable.
3. Carry flavor formal characters through FORM with the same Adams index j as
   their paired gauge characters: `chi_R(z^j)*chi_F(a^j)`.
4. Modify `_project_terms` to take gauge singlets only and retain full flavor
   decompositions. Reuse `CharacterDecompositionCache` for supported simple
   flavor algebras and extend full tensor-product collection where needed;
   `singlet_multiplicities` alone cannot supply flavor output.
5. Extend output rings/parsers/serialization beyond numerical coefficients in
   `(t,y,u)`, or use sparse maps from degree to flavor Dynkin labels, abelian
   charges, and multiplicity, with factor metadata.
6. For refined PL apply Adams operations to flavor variables too:
   `sum_k mu(k)/k * log H(tau^k,a^k)`. Ordinary numerical PL coefficients
   cannot preserve flavor representation content.

No flavor-refinement implementation or option was added during this discussion.

### References consulted

- Gadde, Rastelli, Razamat, Yan, *Gauge Theories and Macdonald Polynomials*,
  https://arxiv.org/abs/1110.3740 — equations (2.10), (4.6)–(4.11), Table 2,
  and Appendix B for HL conditions, letters, and F-term interpretation.
- Kang et al., *Higgs, Coulomb, and Hall-Littlewood*,
  https://arxiv.org/abs/2207.05764 — counterexamples to general HL/Higgs equality,
  including some genus-zero twisted class-S theories.
- Hanany and Kalveks, *Highest Weight Generating Functions for Hilbert Series*,
  https://arxiv.org/abs/1408.4690 — character and highest-weight descriptions.
- Stacks Project, https://stacks.math.columbia.edu/tag/0669 — Koszul resolution
  of a quotient by a Koszul-regular sequence.

Temporary copies of the first paper were downloaded as
`/tmp/arxiv-1110.3740.pdf` and `/tmp/arxiv-1110.3740.txt`; these may not persist.

## Known limitations and useful next tasks

- Update the remaining implementation reference chapters for Tits reality,
  representation/theory enumeration and current dependencies; the database
  structure, production schema 1 and the current index/cache algorithm are documented.
- Reduce Python object reconstruction cost for large cached FORM expansions
  if later profiling justifies a format change; current exact JSON loading can
  dominate warm runs. Preserve raw-program equality and concurrent writes.
- Optimize the expensive intermediate `psi_3(adjoint)^2` tensor decomposition
  identified in the unlimited A32 profile; final sparse pairing is inexpensive.
- Migrate existing database hashes and shared flavor labels if data stored
  under the previous conjugacy or full/half conventions must be reused; resolve
  any pre-existing duplicate theory rows during that migration.
- Implement HL/Higgs calculations if requested, following the qualifications
  and reuse opportunities above; flavor refinement remains optional.
- Search/export actions and missing-or-lower-order index actions are implemented.
  The 21 September search update adds minimum full-index order, a single-sector
  filter and the two sector CSV fields; the CSV selection now has 19 fields.
- Add non-Lagrangian theory support beyond the placeholder table.
- Avoid the duplicate anomaly-check call in the database/property path.
- Imports no longer calculate indices. A future optimization could avoid
  repeated basic checks on the duplicate path while preserving validation.
- Product-factor permutation invariance is implemented by the 22 September
  database-identity update above; historical duplicate rows are not merged.
- Add automatic or assisted identification of dual Lagrangian realizations.
- Live MySQL concurrency tests now run on an isolated server; expand workload
  coverage when new storage behavior or unexplained contention justifies it.
- Consider storing index monomials structurally if database-level coefficient
  queries become necessary.
- Retain the source cutoff when using truncated indices; generated Coulomb
  series currently lack an `O(...)` precision marker.
- Preserve the type-A highest-weight projection when refactoring Casimirs.
- FORM treats nonempty stderr as failure, and LiE parsing accepts only its
  expected output plus a recognized tree-space notice. This is a tool-version
  compatibility concern, not a demonstrated incorrect result.

The GUI entry point is `gui/n2_db.py`. `main.py` is a user-edited helper and is
not the GUI launcher; consult its current contents before running it. OR-Tools
is used by the live Frobenius solver in `common/math_utils.py`.
