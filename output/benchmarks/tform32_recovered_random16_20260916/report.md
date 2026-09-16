# TFORM 32-worker benchmark: random sample of recovered timeout cases

Measured 16 September 2026. The user requested a random sample of 16 theories that timed out initially and later succeeded after allowing longer timeouts.

Completed 16/16; 16 successful FORM expansions.

## Sampling and log evidence

- Initial run: `log_index_20260916_130305_574084.log`, 210 FORM timeouts with start-to-error intervals near 60 seconds.
- Later runs: `log_index_20260916_131926_021494.log` and `log_index_20260916_135204_467248.log`. Together they contain explicit full-index saved messages for 51 of those initial failures.
- Eligibility requires an actual saved message, not merely the absence of another timeout. All 51 eligible IDs were included in the sampling population.
- Reproducible uniform sample without replacement: `random.Random(3329612950807171883).sample(sorted(eligible_ids), 16)`.
- The seed was drawn once from OS randomness. The sample was selected before inspecting theory complexity or benchmark results.
- `selection.json` records the population and draw order; `log_evidence.json` records the failure and success lines for every selected case. Original logs are copied under `logs/`.

## Method

- Current production degree-bounded FORM generator, inclusive cutoff t^18. All sampled cases have one connected gauge sector.
- `/usr/bin/tform -w32 -q program.frm`, one theory at a time, 600-second limit per expansion.
- One fresh expansion per selected theory; no FORM-cache hits or writes. Reported times are individual observations, not averages over repeated runs.
- Timing includes subprocess startup, expansion and complete text output to a local file. Compression is performed afterward and is excluded.
- Excludes Sage preparation, output parsing, LiE singlet projection, final index construction and database/cache operations.
- GNU time reports peak resident memory of TFORM and all its threads. This excludes the Python/Sage parent, filesystem page cache and subsequent parsing/projection.
- Hardware: AMD Ryzen 9 7950X 16-Core Processor; 32 logical CPUs; 30.5 GiB RAM.
- Executable: TFORM 5.0.0 (Jan 27 2026, v5.0.0).
- Theory inputs were exported using a read-only MySQL transaction without schema initialization. Application code, stored theory data, settings and existing caches were not changed.

## Measurements

| Theory ID | Gauge group | TFORM seconds | Peak TFORM GiB | Original failure elapsed s | Later full-index save elapsed s | TFORM / later save (%) | Later save / TFORM (×) |
|---:|---|---:|---:|---:|---:|---:|---:|
| 19704 | SU(3) x SU(4) | 9.00 | 3.55 | 60.20 | 142.23 | 6.33% | 15.80× |
| 19726 | SU(3) x SU(5) | 14.96 | 5.49 | 60.21 | 147.70 | 10.13% | 9.87× |
| 19739 | SU(3) x SU(5) | 14.96 | 5.49 | 60.21 | 76.92 | 19.45% | 5.14× |
| 19749 | SU(3) x SU(5) | 14.90 | 5.50 | 60.22 | 105.61 | 14.11% | 7.09× |
| 19794 | SU(3) x SU(6) | 9.50 | 3.65 | 60.29 | 270.95 | 3.51% | 28.52× |
| 19920 | SU(4) x SU(4) | 9.23 | 3.56 | 60.28 | 251.74 | 3.67% | 27.27× |
| 20062 | SU(4) x SU(5) | 9.48 | 3.66 | 60.34 | 701.20 | 1.35% | 73.97× |
| 20063 | SU(4) x SU(5) | 9.65 | 3.66 | 60.31 | 251.11 | 3.84% | 26.02× |
| 20109 | SU(4) x SU(6) | 9.01 | 3.56 | 60.33 | 304.90 | 2.96% | 33.84× |
| 20113 | SU(4) x SU(6) | 9.17 | 3.55 | 60.31 | 117.40 | 7.81% | 12.80× |
| 20155 | SU(4) x SU(6) | 9.38 | 3.65 | 60.29 | 106.25 | 8.83% | 11.33× |
| 20230 | SU(4) x SU(7) | 15.07 | 5.50 | 60.36 | 148.71 | 10.13% | 9.87× |
| 20591 | SU(5) x SU(6) | 14.95 | 5.49 | 60.36 | 122.91 | 12.16% | 8.22× |
| 20611 | SU(5) x SU(6) | 9.33 | 3.61 | 60.32 | 91.99 | 10.14% | 9.86× |
| 20620 | SU(5) x SU(6) | 15.10 | 5.47 | 60.26 | 180.11 | 8.38% | 11.93× |
| 22459 | SU(11) x SU(11) | 15.22 | 5.50 | 60.20 | 645.29 | 2.36% | 42.40× |

Total measured expansion time: 188.91 seconds.

The ratio columns use the original, unrounded recorded times: `TFORM / later save (%) = 100 × T_TFORM / T_save` and `Later save / TFORM (×) = T_save / T_TFORM`. For example, theory 19704's TFORM expansion took 6.33% of its later full-index save interval; that historical interval was 15.80 times the measured TFORM expansion time.

Across this sample, TFORM took 1.35–19.45% of the historical full-index save interval, corresponding to elapsed-time ratios of 5.14–73.97×.

## Interpretation and verification

The historical successful intervals cover the entire index calculation and database save, with 16 or 32 theories running concurrently. The new measurement covers isolated TFORM expansion only. The ratio columns compare these observed elapsed times across different workloads and concurrency conditions. They do not measure a controlled FORM speedup or the speedup of the complete index calculation.

Success requires zero exit status, empty stderr and a complete printed result. This benchmark does not repeat full gauge projection or compare every expansion with serial FORM. Raw-program hashes, complete-output hashes and compressed outputs are retained.

The worker argument is -w32; observed OS thread counts may be larger than 33. Requested workers and total OS threads are different counts.

Identical raw-program checks: [].

## Files

- `inputs.json`, `selection.json`, `eligible_log_evidence.json`, `log_evidence.json`, `logs/`: inputs and sampling provenance.
- `metadata.json`, `results.json`, `summary.json`: reproducible conditions and measurements.
- `<theory_id>/program.frm`, `tform.out.gz`, `stderr.txt`, `time.txt`, `result.json`: per-case evidence.
- `benchmark.py`: measurement script. Copy it and change OUT before rerunning to preserve these results.
