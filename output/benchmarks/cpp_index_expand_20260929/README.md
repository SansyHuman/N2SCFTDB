# Uncached FORM and C++ expansion benchmark

Measured: 2026-09-29T11:34:00.751340+00:00

Compiler: g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0. C++17, `-O3 -DNDEBUG`.

FORM: FORM 5.0.0 (Jan 27 2026, v5.0.0)

One warmup and full exact comparison per case, followed by 3 measured runs per engine, alternating execution order. Reported times are medians. Both engines use one computation thread. No FORM expansion cache, character decomposition, SQLite or database access.

| Input | Cutoff | Terms | FORM process (s) | C++ process (s) | Process speedup | C++ function (s) | FORM + Python parse (s) | C++ + Python parse (s) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| free_matter | 12 | 41 | 0.0027 | 0.0018 | 1.48x | 0.0000 | 0.0029 | 0.0019 |
| free_matter | 18 | 122 | 0.0072 | 0.0020 | 3.65x | 0.0001 | 0.0079 | 0.0021 |
| su2_four_full_hypers | 12 | 698 | 0.0104 | 0.0030 | 3.45x | 0.0007 | 0.0159 | 0.0046 |
| su2_four_full_hypers | 18 | 6,771 | 0.1199 | 0.0165 | 7.24x | 0.0104 | 0.2128 | 0.0305 |
| product_bifundamental | 12 | 2,075 | 0.0290 | 0.0061 | 4.75x | 0.0027 | 0.0476 | 0.0118 |
| product_bifundamental | 18 | 28,154 | 0.5846 | 0.0730 | 8.01x | 0.0520 | 0.9511 | 0.2506 |
| trifundamental | 12 | 4,645 | 0.0653 | 0.0133 | 4.91x | 0.0069 | 0.1161 | 0.0277 |
| trifundamental | 18 | 81,816 | 1.9249 | 0.3322 | 5.80x | 0.2302 | 3.1902 | 0.7653 |

A speedup above 1 means the C++ process was faster.

The process comparison includes launch, computation, output serialization, pipe transfer and process exit for both programs; input construction is excluded. Both are wrapped by the same GNU time launcher. FORM prints its normal symbolic expression; the C++ test driver prints a compact numeric term format. This compares usable outputs, so output-format costs differ. C++ function timing includes construction of its final native term vector, but excludes launch, input parsing and text output; it must not be interpreted as a direct FORM-kernel timing comparison.

The last two columns add materialization into the same Python `IndexFormTerm` type. They describe this test harness only: there is no Python binding or production backend integration. All measured outputs were compared coefficient-for-coefficient with the warmup reference outside the timed intervals.

Peak resident memory is GNU time's per-child high-water mark in KiB, excluding the Sage/Python parent and Python parsing. FORM's reserved buffers can affect this measurement; there is no claimed total application-memory reduction.

| Input | Cutoff | FORM peak RSS (KiB) | C++ peak RSS (KiB) |
|---|---:|---:|---:|
| free_matter | 12 | 6,144 | 4,352 |
| free_matter | 18 | 5,888 | 4,608 |
| su2_four_full_hypers | 12 | 6,400 | 4,864 |
| su2_four_full_hypers | 18 | 9,216 | 7,168 |
| product_bifundamental | 12 | 6,656 | 5,632 |
| product_bifundamental | 18 | 24,832 | 19,224 |
| trifundamental | 12 | 8,704 | 7,424 |
| trifundamental | 18 | 71,168 | 65,848 |

These cases cover small free, simple, two-factor and three-factor formal inputs. Their results do not establish performance on larger character bases or higher cutoffs, nor compare against parallel TFORM or cached FORM. See `results.json` for raw repetitions, machine details, exact inputs and source hashes.
