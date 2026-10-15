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

The implementation's anonymous namespace contains `expand_index_unordered`.
It performs validation, expansion and term construction, leaving the outer
vector in map iteration order. Character indices and Adams tuples inside each
term remain canonical. All public `expand_index` overloads use this helper and
apply the same final sort as before. The internal helper is not declared in
the public header or exported for external callers.

There is no Python binding, application backend selection, disk I/O, cache,
character decomposition, or Coulomb-module change. The caller owns the
returned records. All temporary state is local, and sparse intermediate
expansions still require RAM.

## Threading

The original four-argument function delegates to the five-argument overload
with `worker_count=1`. Nonpositive counts throw `std::invalid_argument`, even
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
same canonically sorted terms and exact reduced coefficients.

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
