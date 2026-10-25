# Standalone exact index expansion

The separate native character cache and its compatible Python interface are
documented in [CHARACTER_CACHE.md](CHARACTER_CACHE.md).

`index_expand.hpp` declares `n2scftdb::expand_index`; `index_expand.cpp`
implements it in C++17 with GMP integers and rationals. It accepts the four
structured inputs of Python's `_build_form_program`, and returns the same
formal polynomial as that FORM program, represented by `IndexFormTerm`
records. Term ordering is deterministic but differs from FORM's print order.

```cpp
#include "index_expand.hpp"

// Same formal input as _build_form_program(18, 2, (0,), {(1,): 8}).
const auto terms = n2scftdb::expand_index(
    18, 2, {0}, n2scftdb::MatterMultiplicities{{{1}, mpz_class(8)}});

// Optional fifth argument: up to four computation threads, caller included.
const auto parallel_terms = n2scftdb::expand_index(
    18, 2, {0}, n2scftdb::MatterMultiplicities{{{1}, mpz_class(8)}}, 4);

// Explicit timeout in seconds: the serial fifth argument is a double.
const auto timed_serial = n2scftdb::expand_index(
    18, 2, {0}, n2scftdb::MatterMultiplicities{{{1}, mpz_class(8)}}, 30.0);
const auto timed_parallel = n2scftdb::expand_index(
    18, 2, {0}, n2scftdb::MatterMultiplicities{{{1}, mpz_class(8)}}, 4, 30.0);
```

Compile the implementation into the caller with `g++ -std=c++17 -O3 -pthread` and
link with `-lgmpxx -lgmp`. GMP development headers/libraries must be on the
compiler's search paths. On this machine the system compiler is `/usr/bin/g++`
and Sage supplies GMP; a compile-only example from the project root is:

```sh
/usr/bin/g++ -std=c++17 -O3 -pthread -Wall -Wextra -Wpedantic \
  -I /home/subo-lee/miniconda3/envs/sage/include \
  -c index/cpp/index_expand.cpp -o /tmp/index_expand.o
```

For an executable, also use
`-L /home/subo-lee/miniconda3/envs/sage/lib`,
`-Wl,-rpath,/home/subo-lee/miniconda3/envs/sage/lib`, and `-lgmpxx -lgmp`.
These paths are local examples, not hardcoded engine dependencies.

## Mathematical contract

For each Adams index `j`, the engine uses

```text
J_j = 1 / ((1 - t^(3j)*y^j) * (1 - t^(3j)*y^(-j)))
V_j = t^(2j)*u^(2j) - t^(4j)*u^(-2j)
      - t^(3j)*y^j - t^(3j)*y^(-j) + 2*t^(6j)
H_j = t^(2j)*u^(-j) - t^(4j)*u^j
A   = sum_j J_j/j * (V_j*sum_r C_r(j)
      + H_j*sum_M multiplicity[M]*product_(r in M) C_r(j))
B   = exp(A), truncated to the inclusive t cutoff
```

The first character sum is over the supplied vector indices, including
repetitions. All formal characters commute; different Adams indices remain
distinct symbols. Derivative powers in `J_j` are Adams transformed too.
The engine builds `E_k = k*[t^k]A` and computes

```text
B_0 = 1
B_n = (1/n) * sum_(k=2..n) E_k * B_(n-k).
```

Sparse polynomials are bucketed by t degree, so no products beyond the cutoff
are formed. `E_k` has integer coefficients, but the formal `B_n` generally has
rational coefficients. GMP preserves them exactly. The character tuples use
the existing FORM parser's weighted-length padding convention, including
trailing zeros. The header documents input validation and exceptions.

`expand_index_unordered` is public in `index_expand.hpp`, with the same four
overloads, worker limits and timeout behavior as `expand_index`. It performs
validation, expansion and term construction, leaving the outer vector in
unspecified map iteration order. Character indices and Adams tuples inside
each term remain canonical. Both API families share one private implementation;
`expand_index` applies the final canonical sort.

The expansion stage uses only native C++ objects and GMP, with all temporary
state local to the call. The caller owns the returned records. Sparse
intermediate expansions still require RAM. Singlet projection is a separate
stage described below.

## Threading

The original four-argument function uses `worker_count=1`.
Nonpositive counts throw `std::invalid_argument`, even
at cutoffs zero and one. Counts are explicit positive limits, not an automatic
CPU-count setting; concurrent callers each have their own pool and budget.

Degrees remain sequential because `B_n` depends on the earlier coefficients.
Within a degree, chunks of products `E_k * B_(n-k)` are independent. A lazily
created pool reuses up to `worker_count-1` background threads while the caller
participates. Workers read completed coefficients and accumulate into private
maps. Pairwise reductions move polynomial nodes, combine exact coefficients,
and remove cancellations. No shared accumulator lock is needed in the hot loop.
All tasks finish before the degree is divided by `n`; exceptions are propagated
to the caller after workers finish, and threads are joined on exit.

Degrees with fewer than 32,768 term products use the serial path. Larger
degrees split right operands into chunks of at most 512 terms. When fewer tasks
are available than requested threads, the pool uses fewer threads. These are
internal scheduling choices, not changes to the returned polynomial.
The final term count is computed before allocating output storage. Serial
construction reserves capacity once; outputs with at least 4,096 terms use
the same pool to fill disjoint ranges of a sized vector, in chunks of at most
512 terms. Output construction can create the pool even when all recurrence
degrees ran serially. Map entries remain valid until all output tasks finish,
then temporary maps are cleared and the final vector is sorted canonically.
Initial letter construction and final sorting remain serial.
Temporary maps and merging can increase memory use and limit scaling;
more threads are not guaranteed to be faster. All thread counts return the
same exact reduced coefficients; the sorted API also preserves term order.

## Timeouts

The original four- and five-argument signatures remain available and use a
600-second timeout. A fifth `double` selects an explicit serial timeout; the
six-argument overload takes `worker_count` followed by `timeout`. Use `10.0`
for a serial ten-second timeout: an integer fifth argument still means workers.
Timeouts must be finite. Negative values disable the deadline; zero expires
immediately, including vacuum cutoffs. Expiration throws `std::runtime_error`
with `index expansion timed out`, without returning a partial polynomial.

The deadline covers input validation, letters, recurrence products, map merges,
output construction and sorting. Each serial stage or worker task has a private
inlined polling counter. Clock reads and shared cancellation checks are sampled
adaptively toward one per millisecond, starting at 64 operations and using
batches of 1 to 4096 operations, with at most fourfold growth and tighter
batches near expiration. Calculation/stage boundaries and task entry also check
the deadline. Unlimited calls skip clock reads. All deadline state is local to
one expansion, so expiration cannot cancel an independent caller.

When a worker expires, pending tasks are cancelled and active tasks notice the
shared expiration at their checkpoints. The caller waits for all active tasks
and joins its pool before throwing. Deadlines are cooperative: a long individual
operation, allocation or cleanup can delay return beyond the requested time.

## Singlet projection

`index_singlet_project.hpp` declares `project_index` for the unordered output.
Supply the simple gauge factors in order and an `IndexCharacterSpec` for each
formal character: its zero-based factor position and Dynkin labels.

```cpp
#include "index_singlet_project.hpp"

using namespace n2scftdb;
const auto raw = expand_index_unordered(10, 2, {0}, {{{1}, 8}}, 4, 30.0);
const auto index = project_index(raw, {"A1"}, {{0, {2}}, {0, {1}}});
// ProjectedIndex = map<tuple<t_power, y_power, u_power>, mpq_class>.
// The default cache is char_decomposition_cache.db in the working directory.
```

This stage uses native LiE and the existing SQLite character cache by default.
Pass an absolute database path as the fourth argument to reuse a project cache
from another working directory. Alternatively pass a borrowed
`characters::CharacterDecompositionCache&` to reuse memory caches and customize
LiE backend/limits/timeouts. Borrowed caches stay open and belong to one thread.
The convenience overload uses the cache's existing 600-second per-calculation
LiE deadline; it does not impose an overall projection deadline.

Projection interns distinct character structures, batches each gauge factor's
distinct products through `get_singlet_multiplicities`, and multiplies the
independent factor results. The cache splits products and uses exact dual-irrep
pairing instead of decomposing the complete final tensor product. Signed Adams
characters and complex conjugation retain the existing cache semantics. Terms
with equal `t,y,u` powers combine, zero coefficients disappear, and the returned
map iterates in sorted exponent order. No formal outer-vector sort is needed.
Inputs are unchanged, and rational coefficients use GMP throughout.

Compile `index_singlet_project.cpp`, `char_decomposition_cache.cpp` and
`common/cpp/lie.cpp` alongside `index_expand.cpp`; make nlohmann/json headers
available and link `-llie -lgmpxx -lgmp -lsqlite3 -ldl` with the LiE library directory
and runtime path. The expansion-only build above remains independent of these
extra dependencies. See [CHARACTER_CACHE.md](CHARACTER_CACHE.md) for paths and
the shared-cache contract.

## Python calculate_index integration

The same explicit build now includes expansion and projection in the existing
`index._index_calculation_cpp` extension:

```sh
sage -python -B index/cpp/build_index_calculation.py --cxx /usr/bin/g++
```

`index.n2_theory_index.calculate_index` defaults to `backend="native"`. The
binding calls unordered expansion and projection with the GIL released, then
returns only the small final dictionary of exact `Fraction` coefficients.
The public API still returns the same Sage Laurent polynomial and retains
sector splitting, stored-index reuse and exact truncation in Python. Missing
or outdated extensions report the build command; calculation never downloads
or compiles automatically. `backend="python"` selects the original FORM path.

Both the character cache and full-index API load the extension through
`index._index_calculation_native.load_native`. The default call honors
`N2SCFTDB_CHAR_CACHE_BACKEND`; `require_index=True` requires full-index support
independently of that cache setting and reports missing or outdated builds.

```python
from index.n2_theory_index import calculate_index

result = calculate_index(data, 18, worker_count=32)
reference = calculate_index(data, 18, backend="python", worker_count=32, processes=32)
assert result == reference
```

When `worker_count` is omitted, existing `form_threads` values select expansion
workers for either backend. Each native cache now owns an isolated LiE runtime,
so separate caches can calculate concurrently. The projection scheduler remains serial and does not
use `processes`; the reference retains its Python cache-generation pools.
Native expansion has a cooperative timeout, and native LiE retains its
per-calculation timeout. `timeout=None` disables both. The shared character cache
defaults to the project-root database, independent of the working directory.
FORM executable and expansion-cache settings apply only to `backend="python"`.
The CLI accepts `--backend native|python` and `--workers N`.

`test/benchmark_native_calculate_index.py` compares complete API calls with
fresh, warm-character and fully warm caches, checking every exact coefficient.

## Verification

See `test/cpp/README.md` for complete comparisons against real, uncached FORM
and the test-only executable used to inspect the returned terms.

`test/benchmark_cpp_index_parallel.py` compares worker counts 1, 2, 4 and 8
against the same exact serial/FORM results. It reports native function time,
process/Python conversion time and memory separately. The earlier serial-only
report is retained as a dated measurement of the earlier source snapshot.

`test/benchmark_cpp_index_timeout.py` measures timeout overhead against unlimited
mode, checking the exact sorted output on every run. Its optional `--baseline`
compares a saved pre-change test driver as well. Reports are written to
`output/benchmarks/cpp_index_timeout_20261001/`.
