# C++ index expansion parallel scaling

Measured: 2026-09-29T11:54:13.748534+00:00

Compiler: g++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0. C++17, `-O3 -DNDEBUG -pthread`.

CPU: AMD Ryzen 9 7950X 16-Core Processor; 32 logical CPUs. Reference: FORM 5.0.0 (Jan 27 2026, v5.0.0).

Each input was first calculated by uncached, single-thread FORM. One C++ warmup per worker count and 3 measured repetitions per worker count followed, rotating/reversing their execution order. Every C++ output was compared as a complete exact monomial-to-rational map with both FORM and the one-worker C++ warmup. Reported times are medians; all speedups use the measured one-worker C++ baseline.

| Input | Cutoff | Terms | Workers | Native function (s) | Native speedup | Process + Python parse (s) | End-to-end speedup | Peak native RSS (MiB) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| su2_four_full_hypers | 18 | 6,771 | 1 | 0.00888 | 1.00x | 0.02944 | 1.00x | 7.00 |
| su2_four_full_hypers | 18 | 6,771 | 2 | 0.00889 | 1.00x | 0.02996 | 0.98x | 7.00 |
| su2_four_full_hypers | 18 | 6,771 | 4 | 0.00953 | 0.93x | 0.03205 | 0.92x | 7.00 |
| su2_four_full_hypers | 18 | 6,771 | 8 | 0.00947 | 0.94x | 0.02899 | 1.02x | 7.00 |
| su2_four_full_hypers | 20 | 13,303 | 1 | 0.01980 | 1.00x | 0.05854 | 1.00x | 9.96 |
| su2_four_full_hypers | 20 | 13,303 | 2 | 0.01949 | 1.02x | 0.06270 | 0.93x | 9.86 |
| su2_four_full_hypers | 20 | 13,303 | 4 | 0.02022 | 0.98x | 0.05877 | 1.00x | 9.99 |
| su2_four_full_hypers | 20 | 13,303 | 8 | 0.01943 | 1.02x | 0.05830 | 1.00x | 9.68 |
| product_bifundamental | 18 | 28,154 | 1 | 0.05658 | 1.00x | 0.14706 | 1.00x | 18.66 |
| product_bifundamental | 18 | 28,154 | 2 | 0.05166 | 1.10x | 0.14411 | 1.02x | 20.95 |
| product_bifundamental | 18 | 28,154 | 4 | 0.05126 | 1.10x | 0.14371 | 1.02x | 21.63 |
| product_bifundamental | 18 | 28,154 | 8 | 0.05095 | 1.11x | 0.14440 | 1.02x | 25.67 |
| product_bifundamental | 20 | 60,940 | 1 | 0.13846 | 1.00x | 0.35983 | 1.00x | 34.72 |
| product_bifundamental | 20 | 60,940 | 2 | 0.12428 | 1.11x | 0.34213 | 1.05x | 40.47 |
| product_bifundamental | 20 | 60,940 | 4 | 0.12132 | 1.14x | 0.34011 | 1.06x | 47.20 |
| product_bifundamental | 20 | 60,940 | 8 | 0.12390 | 1.12x | 0.34583 | 1.04x | 53.12 |
| trifundamental | 18 | 81,816 | 1 | 0.24010 | 1.00x | 0.56915 | 1.00x | 64.46 |
| trifundamental | 18 | 81,816 | 2 | 0.21236 | 1.13x | 0.55352 | 1.03x | 73.29 |
| trifundamental | 18 | 81,816 | 4 | 0.21631 | 1.11x | 0.54880 | 1.04x | 84.57 |
| trifundamental | 18 | 81,816 | 8 | 0.22282 | 1.08x | 0.54197 | 1.05x | 92.76 |
| trifundamental | 20 | 191,148 | 1 | 0.72399 | 1.00x | 1.53619 | 1.00x | 132.31 |
| trifundamental | 20 | 191,148 | 2 | 0.63927 | 1.13x | 1.43913 | 1.07x | 141.83 |
| trifundamental | 20 | 191,148 | 4 | 0.64232 | 1.13x | 1.47912 | 1.04x | 185.86 |
| trifundamental | 20 | 191,148 | 8 | 0.66872 | 1.08x | 1.47625 | 1.04x | 209.20 |

A speedup above 1 means faster than one-worker C++; below 1 means slower. Of 18 multiworker comparisons, 4 were slower. The largest measured native speedup was 1.14x (product_bifundamental, t20, 4 workers); the lowest was 0.93x (su2_four_full_hypers, t18, 4 workers).

Native timing surrounds only `expand_index`, including native input validation, worker management, arithmetic, combination of partial results, and final native term-vector construction/sorting. It excludes process launch, stdin parsing and text serialization. Requested worker counts are shown; implementation thresholds may keep small stages serial. Thread scheduling and merging overhead can outweigh parallel arithmetic for these input sizes.

Process + Python parse includes GNU time/process launch, native computation, complete text output/transfer, exit, and construction of the same Python `IndexFormTerm` objects. Exact map comparisons are excluded. This test-only subprocess protocol is not a Python binding and does not measure the full application or character decomposition.

To prevent unrelated collections of retained reference data from biasing individual worker counts, Python cyclic garbage is collected before each C++ measurement outside the timers, and cyclic garbage collection is disabled only during timed parsing and then restored. Reference counting remains enabled; this parser constructs acyclic term data. These controlled parsing timings are not predictions of production Python garbage-collection overhead.

Peak RSS is GNU time's native child high-water resident memory, excluding the Sage/Python parent and Python parsing. Parallel partial polynomials and allocator behavior may increase memory. There is no persistent expansion cache or database access.

The FORM reference below is a single correctness run for each input, not a repeated FORM timing benchmark or a TFORM comparison.

| Input | Cutoff | FORM process (s), single reference run | FORM peak native RSS (MiB) |
|---|---:|---:|---:|
| su2_four_full_hypers | 18 | 0.12303 | 9.00 |
| su2_four_full_hypers | 20 | 0.27007 | 12.75 |
| product_bifundamental | 18 | 0.60449 | 24.00 |
| product_bifundamental | 20 | 1.48830 | 49.75 |
| trifundamental | 18 | 1.96348 | 70.25 |
| trifundamental | 20 | 5.23720 | 166.00 |

Reproduce from the repository root:

```sh
PATH=/home/subo-lee/miniconda3/envs/sage/bin:$PATH \
  sage -python -B test/benchmark_cpp_index_parallel.py \
  --orders 18 20 \
  --workers 1 2 4 8 \
  --cases su2_four_full_hypers product_bifundamental trifundamental \
  --repetitions 3
```

Compiler/GMP overrides are shared with `test/benchmark_cpp_index_expand.py` and documented in `test/cpp/README.md`. See `results.json` for raw repetitions, execution order, input data, compiler command, source hashes and machine details. These measurements do not establish scaling for other character bases, machines or substantially higher cutoffs.
