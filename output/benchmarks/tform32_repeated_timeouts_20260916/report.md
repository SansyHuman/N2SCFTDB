# TFORM with 32 workers: repeated timeout cases

Measured 16 September 2026 on the existing Linux workstation. This report covers the 16 repeated FORM timeout failures selected by the user from `logs/log_index_20260916_131926_021494.log`.

Completed 16/16; 16 successful expansions.

## Method

- Inclusive cutoff: t^18; current production degree-bounded FORM generator.
- Every selected theory has one connected gauge sector. Each theory is executed separately, even when raw programs coincide.
- `/usr/bin/tform -w32 -q program.frm`; 32 requested worker threads, one theory at a time; 600-second limit per expansion.
- One measured run per theory. Times are observations, not medians over repeated runs.
- GNU time measures subprocess wall time and peak resident memory. Complete FORM text is written to a local file, then compressed after timing. Application capture into a Python string is not included.
- Excludes Sage input preparation, FORM-output parsing, LiE singlet projection, final polynomial construction, SQLite lookup/insertion and MySQL writes.
- Inputs were fetched in a read-only MySQL transaction with no schema initialization. Existing databases and caches were not modified.
- Hardware: AMD Ryzen 9 7950X 16-Core Processor, 32 logical CPUs, 30.5 GiB RAM.
- Executable: TFORM 5.0.0 (Jan 27 2026, v5.0.0).

## Results

| Theory ID | Gauge group | TFORM seconds | Peak TFORM GiB | Logged start-to-timeout seconds | Status |
|---:|---|---:|---:|---:|---|
| 20036 | SU(4) x SU(5) | 24.32 | 7.19 | 122.86 | success |
| 20037 | SU(4) x SU(5) | 24.45 | 7.19 | 122.88 | success |
| 20150 | SU(4) x SU(6) | 23.94 | 7.19 | 123.52 | success |
| 20168 | SU(4) x SU(6) | 24.32 | 7.19 | 121.50 | success |
| 20517 | SU(5) x SU(5) | 37.69 | 8.84 | 121.61 | success |
| 20535 | SU(5) x SU(5) | 38.33 | 8.84 | 121.32 | success |
| 20631 | SU(5) x SU(6) | 37.79 | 8.84 | 121.91 | success |
| 20632 | SU(5) x SU(6) | 37.58 | 8.84 | 122.61 | success |
| 20641 | SU(5) x SU(6) | 24.64 | 7.18 | 122.79 | success |
| 20643 | SU(5) x SU(6) | 24.92 | 7.19 | 122.06 | success |
| 20651 | SU(5) x SU(6) | 25.46 | 7.26 | 122.20 | success |
| 20658 | SU(5) x SU(6) | 25.47 | 7.25 | 122.50 | success |
| 20668 | SU(5) x SU(6) | 24.76 | 7.18 | 121.60 | success |
| 20671 | SU(5) x SU(6) | 24.74 | 7.18 | 121.63 | success |
| 22470 | SU(11) x SU(11) | 37.55 | 8.84 | 122.87 | success |
| 22475 | SU(11) x SU(11) | 37.46 | 8.83 | 122.92 | success |

Total measured expansion time: 473.42 seconds.

## Interpretation and verification

The original retry log ran 32 theory processes concurrently. Its 121-124-second start-to-error intervals include preparation and scheduling, and do not measure isolated serial FORM execution. Do not divide those censored times by this benchmark to claim a controlled TFORM speedup.

Success requires zero exit status, empty stderr and a complete printed result. The benchmark preserves raw-program hashes, full-output hashes, generated programs and compressed complete output. It does not compare every large expansion against serial FORM or compute the final gauge-projected index.

The OS monitor observed up to 63 threads for these runs; the command explicitly requests 32 worker threads. Requested workers and total OS threads are different counts. Peak memory is the entire TFORM process, including its threads, but excludes the Sage/Python parent and page cache.

Identical-program cross-checks: [{"theory_ids": [20517, 22475], "program_sha256": "1b31af8bb17e70e8cae1a57aaf804707de4b36c84ffa290b6ef04b495e1803ad", "identical_full_output": true}, {"theory_ids": [20535, 22470], "program_sha256": "bfbf42f452096d37ed1e1a6c8c795be19745d8b099bb9ce1c69e8730c2aab32f", "identical_full_output": true}]

## Retained files

- `inputs.json`: exact theory inputs exported read-only.
- `source_log.txt` and `logged_failures.json`: source evidence and failure intervals.
- `metadata.json`, `results.json`, `summary.json`: machine-readable conditions and measurements.
- `<theory_id>/program.frm`, `tform.out.gz`, `time.txt`, `stderr.txt`, `result.json`: per-case evidence.
- `benchmark.py`: benchmark source. Re-running overwrites this output directory; copy and change its OUT constant to retain independent runs.
