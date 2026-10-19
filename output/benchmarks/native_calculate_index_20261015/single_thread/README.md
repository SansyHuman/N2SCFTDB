# Native calculate_index versus original Python pipeline

Measured 2026-10-15T08:58:01.810842+00:00; 3 fresh process repetitions per backend.

Times are median wall seconds inside the public `calculate_index` call, including validation, expansion, cache operations, projection, Sage conversion and sector multiplication. Interpreter/import/build time is excluded; root-system metadata is initialized equally before timing.

The original path uses FORM/TFORM plus Python projection, pure Python cache logic and executable LiE. The native path uses C++ expansion, SQLite cache and in-process LiE projection. All complete exact coefficient maps match across backends, repetitions and cache states.

Workers=1 uses one expansion thread and one LiE worker. Workers=32 uses 32 C++ expansion workers or TFORM `-w32`; the Python reference also allows 32 LiE cache-generation processes. Native projection currently remains serial; these are requested worker limits, not promises of 32-way utilization throughout the calculation.

Cold means new empty caches. Warm characters retains character/singlet caches but gives FORM a fresh expansion-cache path. Warm all also reuses the original FORM expansion cache; native never writes an expansion cache. All files are private temporary files; no project cache or MySQL is used.

| Theory | Cutoff | Workers | Cache state | Native (s) | Python (s) | Python/native |
|---|---:|---:|---|---:|---:|---:|
| SU2_Nf4 | 12 | 1 | cold | 0.2023 | 0.1750 | 0.86x |
| SU2_Nf4 | 12 | 1 | warm_characters | 0.0033 | 0.0665 | 19.89x |
| SU2_Nf4 | 12 | 1 | warm_all | 0.0033 | 0.0065 | 1.95x |
| SU3_Nf6 | 12 | 1 | cold | 0.2923 | 0.2600 | 0.89x |
| SU3_Nf6 | 12 | 1 | warm_characters | 0.0066 | 0.0938 | 14.25x |
| SU3_Nf6 | 12 | 1 | warm_all | 0.0068 | 0.0153 | 2.26x |
| Sp4_Nf6 | 12 | 1 | cold | 0.2057 | 0.1755 | 0.85x |
| Sp4_Nf6 | 12 | 1 | warm_characters | 0.0033 | 0.0668 | 20.14x |
| Sp4_Nf6 | 12 | 1 | warm_all | 0.0033 | 0.0067 | 2.01x |
| SU2xSU2_bifundamental | 12 | 1 | cold | 0.1988 | 0.2144 | 1.08x |
| SU2xSU2_bifundamental | 12 | 1 | warm_characters | 0.0064 | 0.1074 | 16.87x |
| SU2xSU2_bifundamental | 12 | 1 | warm_all | 0.0059 | 0.0147 | 2.48x |

Ratios above 1 favor native; ratios below 1 favor Python. These measurements apply to these inputs and cache states. Peak RSS fields in the JSON are per-process high-water marks, not summed process-tree RAM.

`results.json` retains exact coefficients, every sample, inputs, CPU/build metadata and source hashes.
