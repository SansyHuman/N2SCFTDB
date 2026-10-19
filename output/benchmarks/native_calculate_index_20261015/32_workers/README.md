# Native calculate_index versus original Python pipeline

Measured 2026-10-15T08:45:02.584362+00:00; 2 fresh process repetitions per backend.

Times are median wall seconds inside the public `calculate_index` call, including validation, expansion, cache operations, projection, Sage conversion and sector multiplication. Interpreter/import/build time is excluded; root-system metadata is initialized equally before timing.

The original path uses FORM/TFORM plus Python projection, pure Python cache logic and executable LiE. The native path uses C++ expansion, SQLite cache and in-process LiE projection. All complete exact coefficient maps match across backends, repetitions and cache states.

Workers=1 uses one expansion thread and one LiE worker. Workers=32 uses 32 C++ expansion workers or TFORM `-w32`; the Python reference also allows 32 LiE cache-generation processes. Native projection currently remains serial; these are requested worker limits, not promises of 32-way utilization throughout the calculation.

Cold means new empty caches. Warm characters retains character/singlet caches but gives FORM a fresh expansion-cache path. Warm all also reuses the original FORM expansion cache; native never writes an expansion cache. All files are private temporary files; no project cache or MySQL is used.

| Theory | Cutoff | Workers | Cache state | Native (s) | Python (s) | Python/native |
|---|---:|---:|---|---:|---:|---:|
| SU2_cubed_trifundamental | 26 | 32 | cold | 18.4554 | 62.7693 | 3.40x |
| SU2_cubed_trifundamental | 26 | 32 | warm_characters | 17.0960 | 60.6694 | 3.55x |
| SU2_cubed_trifundamental | 26 | 32 | warm_all | 17.4638 | 21.6753 | 1.24x |
| SU33_Nf66 | 18 | 32 | cold | 81.1408 | 90.7248 | 1.12x |
| SU33_Nf66 | 18 | 32 | warm_characters | 0.0882 | 0.6498 | 7.37x |
| SU33_Nf66 | 18 | 32 | warm_all | 0.0898 | 0.2993 | 3.33x |

Ratios above 1 favor native; ratios below 1 favor Python. These measurements apply to these inputs and cache states. Peak RSS fields in the JSON are per-process high-water marks, not summed process-tree RAM.

`results.json` retains exact coefficients, every sample, inputs, CPU/build metadata and source hashes.
