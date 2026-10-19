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
identical returned ordering required. There are also 19 native invalid-input
checks, including worker counts 0 and -1 at ordinary and vacuum cutoffs.

A native stress test runs four independent callers concurrently, each making
repeated two/four-worker expansions. Its 40 calls cover cutoff 18
trifundamentals, repeated vector and matter characters, signed and 100-bit
multiplicities, exact cancellation, and the vacuum. Every field of every term
is compared in order against a serial reference. Additional native and Python
checks request 32 workers for a small expansion and the vacuum, ensuring that
more requested threads than useful work is accepted. These tests run even if
FORM is unavailable.

A native output-construction check uses 4201 independent repeated-character
hyper monomials at cutoffs 2 and 3. Its recurrence stays serial, while its 4202
output terms exercise parallel construction with 2, 4 and 32 requested workers,
including a partial final chunk. Almost all terms belong to one degree, with
empty degree buckets before and after it. The test checks the known leading
hyper formula, signed 100-bit coefficients, canonical order and padded character
tuples, and eight further concurrent calls against the serial result. It runs
without FORM.

The timeout checks exercise zero deadlines (including vacuum cutoffs), serial
and four-worker expiration during a large expansion, recovery on later calls,
and simultaneous successful and expiring independent callers. They also check
finite/unlimited results, nonfinite timeout rejection, and both timed overloads.
`index_expand_internal_driver.cpp` now includes only the public header and links
the expansion implementation as a separate translation unit. It compares all
public `expand_index_unordered` overloads with the sorted result, including
parallel output construction, timeouts and invalid inputs.

## Native singlet projection

```sh
sage -python -B -m unittest test.test_cpp_index_singlet_project -v
```

This builds `index_singlet_project_driver.cpp` with system g++, the expansion
and projection sources, the native character cache and LiE wrapper. It uses
GMP, SQLite and the nlohmann/json headers installed by
`index/cpp/build_char_cache.py --install-deps`; `NLOHMANN_JSON_INCLUDE_DIR`
selects another JSON include directory. `CPP_INDEX_CXXFLAGS` also applies to
this build for sanitizer instrumentation. JSON is only a test transport; the
production API accepts and returns native exact C++ objects.

All databases and test binaries are temporary. Tests compare the complete
FORM/Python-LiE pipeline against unordered C++ expansion followed by C++
projection for simple and product groups, and check independent SU(2)/SU(3)
weight oracles. They cover complex representations, signed Adams characters,
repeated products, aliases, exact large rationals, cancellations, invalid
metadata, native calculation without a LiE executable on PATH, both projection
overloads, and persistent cache interoperability in both directions. Cache-only
tests fail on any miss and verify zero coefficients remain valid cache hits.

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
  test.test_cpp_index_expand.CppIndexExpandTests.test_native_concurrent_calls_and_repeatability \
  test.test_cpp_index_expand.CppIndexExpandTests.test_native_output_construction_with_serial_recurrence -v
```

The second method checks the case where worker threads are first created for
output construction.

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
four-argument serial function. `--timeout SECONDS` selects an explicit deadline,
with negative values for unlimited execution. It can be combined with
`--workers N`. Use `--timeout-self-test` for deadline/cancellation checks and
`--self-test` for native invalid-input
checks, `--parallel-self-test` for concurrent-call checks, or
`--output-self-test` for output-construction checks; those modes do not emit
timing records.

To measure timeout overhead without timing input/output or process launch:

```sh
sage -python -B test/benchmark_cpp_index_timeout.py --repetitions 7
```

The script compares the default 600-second timeout with unlimited calls using
one and four workers. `--baseline /path/to/saved/index_expand_driver` additionally
compares a driver built before the timeout change. Every exact printed term
matches across modes and runs. Reports are retained under
`output/benchmarks/cpp_index_timeout_20261001/`.
