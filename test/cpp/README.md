# Exact C++ expansion regression tests

From the repository root, run:

```sh
sage -python -B -m unittest test.test_cpp_index_expand -v
```

The suite compiles `index_expand_driver.cpp` together with
`../../index/cpp` using the system `/usr/bin/g++`, C++17, `-pthread`,
and GMP.
The driver is only a test executable: no Python extension or production backend
is built or installed. Its executable, input scripts and FORM output are
temporary, and no expansion cache, character decomposition or database is used.

The current Sage environment can be selected with:

```sh
PATH=/home/subo-lee/miniconda3/envs/sage/bin:$PATH \
  sage -python -B -m unittest test.test_cpp_index_expand -v
```

The test discovers GMP headers and libraries under the active Python/Sage
prefix, falling back to the compiler's normal system search paths. Override
`GMP_INCLUDE_DIR` and `GMP_LIBRARY_DIR` for another GMP installation. `CXX`
overrides the compiler command, `CPP_INDEX_CXXFLAGS` adds compilation/link flags
(for example `-fsanitize=undefined`), and `FORM_EXECUTABLE` selects the reference
FORM executable. Missing FORM skips only the comparisons against FORM; missing
the selected compiler skips this test class. Missing required development files
causes a build error with the compiler diagnostic.

The comparisons cover the existing 42 degree-bound regression cases, cutoff 18
simple/product/trifundamental expansions, cutoff 20 trifundamental expansion,
free matter, repeated characters,
zero and signed multiplicities, integers larger than 64 bits, and seeded random
inputs. They compare complete monomial-to-rational-coefficient maps and check
canonical character tuples and exact coefficient normalization. Additional
native checks exercise invalid inputs, and cutoff 0/1 checks require only the
vacuum term. All 79 FORM fixtures are checked with the original serial API and
the explicit worker counts 1, 2 and 4 (316 exact FORM comparisons), with
identical returned ordering required. There are also 13 native invalid-input
checks, including worker counts 0 and -1 at ordinary and vacuum cutoffs.

A native stress test runs four independent callers concurrently, each making
repeated two/four-worker expansions. Its 40 calls cover cutoff 18
trifundamentals, repeated vector and matter characters, signed and 100-bit
multiplicities, exact cancellation, and the vacuum. Every field of every term
is compared in order against a serial reference. Additional native and Python
checks request 32 workers for a small expansion and the vacuum, ensuring that
more requested threads than useful work is accepted. These tests run even if
FORM is unavailable.

To repeat the suite with undefined-behavior instrumentation:

```sh
CPP_INDEX_CXXFLAGS='-fsanitize=undefined -fno-sanitize-recover=all' \
  sage -python -B -m unittest test.test_cpp_index_expand -v
```

Where supported by the compiler and host runtime, the native concurrent-call
test can also be instrumented with ThreadSanitizer:

```sh
CPP_INDEX_CXXFLAGS='-fsanitize=thread -g' \
  sage -python -B -m unittest \
  test.test_cpp_index_expand.CppIndexExpandTests.test_native_concurrent_calls_and_repeatability -v
```

ThreadSanitizer requires a compatible virtual address layout and can fail to
initialize in some container/sandbox environments. Such a startup error does
not constitute a successful race check.

To compare uncached, single-threaded FORM with the standalone C++ engine:

```sh
sage -python -B test/benchmark_cpp_index_expand.py --repetitions 3
```

This compiles with the system `/usr/bin/g++ -std=c++17 -O3 -DNDEBUG -pthread`, uses one
warmup and three measured runs per engine, and compares complete exact outputs
for every run. Default inputs cover free, simple, bifundamental and
trifundamental matter at cutoffs 12 and 18. It records process time, Python
materialization time, C++ function time, and child peak resident memory
separately. Large outputs stay in temporary storage. Reports are written to
`output/benchmarks/cpp_index_expand_20260929/`; `--output`, `--cases`, `--orders`
and `--timeout` customize the run. The report documents output-format and timing
differences; C++ function-only timing is not a direct FORM-kernel comparison.

For scaling of the multithreaded overload, run:

```sh
sage -python -B test/benchmark_cpp_index_parallel.py \
  --orders 18 20 --workers 1 2 4 8 --repetitions 3
```

This verifies every output against FORM and the one-worker result, and writes
its separate report to `output/benchmarks/cpp_index_parallel_20260929/`.
The earlier serial benchmark report remains a historical source snapshot.

To measure C++ scaling with multiple worker counts:

```sh
sage -python -B test/benchmark_cpp_index_parallel.py \
  --workers 1 2 4 8 --repetitions 3
```

This checks every measured result against uncached FORM and the serial C++
output, measures native function time and complete process time separately,
and writes its report to `output/benchmarks/cpp_index_parallel_20260929/`.
It does not time parallel TFORM. Keep other heavy jobs idle during timings;
extra workers may cost more in scheduling and merging than they save on small
expansions.

The driver's whitespace-delimited input is `order character_count vector_count`,
followed by the vector indices, `matter_count`, and one
`multiplicity monomial_length character_indices...` record per matter monomial.
Each output line is `t y u numerator denominator character_count`, followed by
one `character_index tuple_length adams_powers...` group per character. Integer
multiplicities, numerators and denominators use arbitrary-precision decimal
notation. Successful expansions also write one JSON record to stderr with
`expansion_seconds` (time inside the C++ function, including its final native
term vector), `term_count`, and `worker_count`. The optional `--workers N`
selects the five-argument C++ overload; omitting it exercises the original
four-argument serial function. Use `--self-test` for native invalid-input
checks or `--parallel-self-test` for concurrent-call checks; those modes do not
emit timing records.
