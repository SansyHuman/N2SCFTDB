# Native index integration and measured performance

Measured 15 October 2026 on AMD Ryzen 9 7950X (16 cores, 32 logical CPUs), Linux; system g++ 13.3.0, C++17 `-O3 -DNDEBUG -pthread`, FORM/TFORM 5.0.0.

`calculate_index` and `calculate_index_internal` now default to native C++ unordered expansion, cached native LiE singlet projection, and exact conversion of only the final coefficients into the existing Sage Laurent polynomial. The original path is available as `backend="python"`.

```python
result = calculate_index(data, 18, worker_count=32)
reference = calculate_index(data, 18, backend="python", worker_count=32, processes=32)
```

Build explicitly with the same Python interpreter used to calculate indices:

```sh
sage -python -B index/cpp/build_char_cache.py --cxx /usr/bin/g++
```

Existing `form_threads` values are used when `worker_count` is omitted. Native LiE projection is currently serial; 32 refers to expansion workers. The original 32-worker benchmark uses TFORM `-w32` and permits 32 Python LiE cache-generation processes. Native mode retains the shared character/singlet SQLite cache and writes no FORM expansion cache.

All 96 measured calculations returned exactly matching complete coefficient maps. Four small theories used one worker and three fresh-process repetitions per backend; two heavier theories used 32 workers and two repetitions per backend. The reference forced pure Python character-cache logic and executable LiE. Source hashes in both reports match the final measured sources.

Times below are median seconds inside the public API, including validation, expansion, cache operations, projection, Sage conversion and sector multiplication. Imports/builds are excluded, and root-system metadata was initialized equally before timing. Cold means new empty caches. Warm characters keeps character/singlet caches while forcing fresh FORM expansion. Warm all also reuses the original FORM expansion cache.

| Theory | Cutoff | Workers | Cold native | Cold Python | Cold speedup | Warm-character speedup | Fully warm speedup |
|---|---:|---:|---:|---:|---:|---:|---:|
| SU2_Nf4 | 12 | 1 | 0.2023 | 0.1750 | 0.86x | 19.89x | 1.95x |
| SU3_Nf6 | 12 | 1 | 0.2923 | 0.2600 | 0.89x | 14.25x | 2.26x |
| Sp4_Nf6 | 12 | 1 | 0.2057 | 0.1755 | 0.85x | 20.14x | 2.01x |
| SU2xSU2_bifundamental | 12 | 1 | 0.1988 | 0.2144 | 1.08x | 16.87x | 2.48x |
| SU2_cubed_trifundamental | 26 | 32 | 18.4554 | 62.7693 | 3.40x | 3.55x | 1.24x |
| SU33_Nf66 | 18 | 32 | 81.1408 | 90.7248 | 1.12x | 7.37x | 3.33x |

Ratios above 1 favor native. Empty-cache native calculation was slightly slower on three small theories; native was faster for all warm cases and both heavier cases. More workers do not imply proportional scaling, especially when LiE projection dominates.

For the trifundamental case, the original expansion cache was 147,050,496 bytes (140.24 MiB), in addition to the 385,024-byte shared character cache. Native retained only the character cache. Each benchmark used private temporary files and no project cache or MySQL database.

Full per-phase times, every sample, exact coefficients, input JSON, compiler command, source hashes and memory high-water marks are in [single-thread results](single_thread/README.md) and [32-worker results](32_workers/README.md). RSS fields are per-process high-water marks, not total process-tree memory.

Reproduce:

```sh
sage -python -B test/benchmark_native_calculate_index.py --cases SU2_Nf4 SU3_Nf6 Sp4_Nf6 SU2xSU2_bifundamental --repetitions 3 --output output/benchmarks/native_calculate_index_20261015/single_thread
sage -python -B test/benchmark_native_calculate_index.py --cases SU2_cubed_trifundamental SU33_Nf66 --repetitions 2 --output output/benchmarks/native_calculate_index_20261015/32_workers
```

Validation: 167 focused tests completed in 14.658 seconds: 158 passed, nine
optional integration tests skipped, zero failures or errors. This includes
production native index selection, exact reference results, worker counts,
timeout/recovery, strict arbitrary-precision binding conversion, concurrent
callers, character-cache interoperability and the retained FORM-cache path.

The broad offscreen GUI run was stopped after stalling. An isolated GUI suite
failed before its test actions while saving temporary settings, and a minimal
standalone PyQt6 `QSettings` write (without importing the index code) reproduced
`AccessError` with no settings file created. Full GUI verification therefore
remains unavailable in this environment. No GUI runtime code was changed, and
no live MySQL integration was run.
