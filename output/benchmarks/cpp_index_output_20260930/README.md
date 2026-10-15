# Preallocated parallel output construction benchmark

Measured: 2026-09-30T08:31:17.976745+00:00

Compiler: g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0. Both builds use C++17, `-O3 -DNDEBUG -pthread` and the same GMP library.

CPU: AMD Ryzen 9 7950X 16-Core Processor; 32 logical CPUs.

The before build is the source snapshot taken immediately before the output-construction change. The after build is the updated implementation. The identical test driver times only `expand_index`; input parsing, native text serialization, process launch and Python hashing are outside the native timer. Native timing includes the recurrence, output conversion and final sort. No Python parse timing is used.

One warmup per build and worker count, followed by 5 measured runs per build and worker count. Each repetition rotates and reverses the eight build/worker combinations. Every complete canonical output byte stream must have the same SHA-256 as the before, one-worker warmup; the number of terms is checked independently. The regression suite provides separate exact FORM validation.

| Input | Cutoff | Terms | Workers | Before native (s) | After native (s) | Before/after speedup | After speedup vs after 1 worker | Before peak RSS (MiB) | After peak RSS (MiB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| su2_four_full_hypers | 18 | 6,771 | 1 | 0.00893 | 0.00784 | 1.14x | 1.00x | 7.00 | 6.50 |
| su2_four_full_hypers | 18 | 6,771 | 2 | 0.00896 | 0.00803 | 1.12x | 0.98x | 7.00 | 7.25 |
| su2_four_full_hypers | 18 | 6,771 | 4 | 0.00906 | 0.00794 | 1.14x | 0.99x | 7.00 | 6.75 |
| su2_four_full_hypers | 18 | 6,771 | 8 | 0.00910 | 0.00812 | 1.12x | 0.97x | 6.75 | 6.50 |
| product_bifundamental | 20 | 60,940 | 1 | 0.13525 | 0.13088 | 1.03x | 1.00x | 34.85 | 31.00 |
| product_bifundamental | 20 | 60,940 | 2 | 0.12638 | 0.11096 | 1.14x | 1.18x | 39.43 | 38.75 |
| product_bifundamental | 20 | 60,940 | 4 | 0.12540 | 0.10245 | 1.22x | 1.28x | 45.90 | 38.73 |
| product_bifundamental | 20 | 60,940 | 8 | 0.12973 | 0.10588 | 1.23x | 1.24x | 51.11 | 43.49 |
| trifundamental | 20 | 191,148 | 1 | 0.71898 | 0.57187 | 1.26x | 1.00x | 132.41 | 108.75 |
| trifundamental | 20 | 191,148 | 2 | 0.64356 | 0.47753 | 1.35x | 1.20x | 150.91 | 135.00 |
| trifundamental | 20 | 191,148 | 4 | 0.66810 | 0.48930 | 1.37x | 1.17x | 178.06 | 134.38 |
| trifundamental | 20 | 191,148 | 8 | 0.65814 | 0.50141 | 1.31x | 1.14x | 212.78 | 149.52 |

Times and memory values are medians. Peak RSS is GNU time's native child high-water resident memory; the Python orchestrator is excluded. Small stages can remain serial according to implementation thresholds. These workloads do not establish scaling for other character bases or higher cutoffs.

No persistent expansion cache, database access, character decomposition or Python binding is involved. See `results.json` for raw runs, execution order, exact inputs, compiler commands, source and executable hashes, machine details and output hashes.

Temporary benchmark driver, before source snapshot and orchestration script used for this report:

- `/tmp/cpp-output-before-knyd0b04/benchmark_driver.cpp`
- `/tmp/cpp-output-before-knyd0b04/index/cpp/index_expand.cpp`
- `/tmp/cpp-output-before-knyd0b04/run_benchmark.py`

Run `python3 /tmp/cpp-output-before-knyd0b04/run_benchmark.py` while those temporary files remain available. All expansion outputs and executables are temporary; only the small report files are retained in the repository.

## Temporary instrumented stage profile

The following trifundamental t20 stage times explain the change. These come from separately instrumented temporary copies, with one warmup plus five rotated measured runs per build/worker count; they are not the primary timings above. Every instrumented canonical output matches the uninstrumented output hash. Medians are shown in milliseconds.

| Build | Workers | Recurrence | Output preparation | Output conversion | Output map clear | Total output | Final sort |
|---|---:|---:|---:|---:|---:|---:|---:|
| before | 1 | 365.25 | 0.00 | 236.85 | 17.90 | 254.18 | 89.56 |
| before | 2 | 278.95 | 0.00 | 236.92 | 22.61 | 258.72 | 89.21 |
| before | 4 | 290.21 | 0.00 | 215.36 | 25.29 | 246.24 | 88.28 |
| before | 8 | 328.50 | 0.00 | 209.07 | 29.39 | 237.76 | 84.13 |
| after | 1 | 357.57 | 0.22 | 80.78 | 17.54 | 97.21 | 96.30 |
| after | 2 | 286.67 | 26.17 | 34.92 | 21.42 | 83.39 | 85.98 |
| after | 4 | 296.09 | 27.05 | 16.78 | 22.41 | 66.58 | 89.39 |
| after | 8 | 329.87 | 26.45 | 9.59 | 26.57 | 62.96 | 90.86 |

Output preparation after the change includes counting output terms, building fixed ranges and offsets, reserving or resizing the output vector, and creating a worker pool if the recurrence did not need one. Output conversion moves coefficients and decodes characters. In the before implementation, this phase also includes vector growth and coefficient copying. For serial runs, map-clear time is separated by per-degree timestamps; for parallel output construction it is timed after the conversion barrier. The total output column includes preparation, conversion, clearing, and helper/task destruction; medians of parts need not sum exactly to the total median. Final sorting remains serial.

Instrumentation adds timestamps and a small stderr JSON record to temporary source copies. No arithmetic operation, branch deciding whether work is parallel, or result ordering is changed. Source/build hashes and raw samples for these copies are in the `stage_profile` field of `results.json`. The generator and runner are `/tmp/cpp-output-before-knyd0b04/instrument_stages.py` and `/tmp/cpp-output-before-knyd0b04/run_stage_profile.py`.
