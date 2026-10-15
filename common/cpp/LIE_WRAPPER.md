# Native LiE Adams and tensor operations

The native integration is licensed under LGPL-2.1-or-later. The project-root
[LICENSE](../../LICENSE) lists the covered files and includes the complete
license text and upstream LiE attribution and permission notices.

`lie.hpp` / `lie.cpp` provide an owning C++17 interface in `n2scftdb::lie`:

```cpp
#include "lie.hpp"
namespace lie = n2scftdb::lie;

auto psi2 = lie::adams("A1", 2, lie::Labels{1});
// Decomposition{{{0}, -1}, {{2}, 1}}
auto product = lie::tensor("A2", lie::Labels{1, 0}, lie::Labels{0, 1});
// Decomposition{{{0, 0}, 1}, {{1, 1}, 1}}
auto psi4 = lie::adams("A1", 2, psi2); // Adams acts on virtual characters too
```

Both functions accept either highest-weight labels or decompositions of
virtual characters. `Labels` is `std::vector<std::int64_t>` and `Decomposition`
is `std::map<Labels, mpz_class>`. Results own their labels and exact signed
coefficients; they remain valid across calls and contain no borrowed LiE
pointers. Empty maps represent zero. Adams powers must be positive.

The private C bridge calls upstream `Adams` and `Tensor` directly. It copies
LiE polynomial rows and transfers bigint coefficients as binary base-2^15
digits through GMP export/import. There is no command interpreter, text
formatting, output parsing, subprocess, initfile, or signal-handler setup.
SQLite cache serialization remains unchanged and separate from this interface.

`Options{max_objects, timeout}` optionally controls each call. The defaults
are 9,999,999 live LiE objects and 600 seconds. A negative C++ timeout means
unlimited; zero expires immediately. Deadlines are cooperative and exclude
time waiting for the runtime mutex. Hot allocation and Weyl loops use an
inlined counter; clock reads are sampled adaptively toward a 1 ms interval,
with batches of 1 to 4096 hooks, starting at 64 and tightening near expiration.
Calculation boundaries always check the deadline, including after exporting
the result. The interval is a target, not a hard latency guarantee: a single
expensive action and subsequent cleanup can delay return. Failures throw
`std::runtime_error`; invalid inputs throw
`std::invalid_argument`, and oversized input coefficients throw
`std::overflow_error`. Failed calculations do not leave partial cache entries.

LiE uses global state. The wrapper serializes native calls per process,
registers fork handlers for its mutex, and frees all per-call mathematical
allocations even on failure. `longjmp` recovery stays inside C and never skips
C++ destructors. Separate processes can compute concurrently. Do not call
raw upstream LiE functions or initialize the interpreter alongside this API.

Supported groups are finite simple A, B, C, D, E, F and G types at their valid
ranks. Labels are nonnegative machine integers. To leave room for upstream
signed-long weight arithmetic, the wrapper conservatively bounds the sum of
labels times the Adams power by
`floor((sqrt(LONG_MAX)-1)/(32*(rank+1)^2))`; tensor inputs each use half that
bound. Coefficients use exact GMP integers at the interface, subject to LiE's
own bigint storage limits (signed-short digit counts, at most 32,767 base-2^15
digits). This is not a replacement for arbitrary-rank/weight symbolic algebra.

Build `liblie.so` using `LiE/Makefile.shared`, then compile `lie.cpp` with the
caller and link LiE, GMPXX and GMP. With this machine's dependencies:

```sh
make -C common/cpp/LiE -f Makefile.shared -j4
g++ -std=c++17 -O2 -pthread \
  -I/home/subo-lee/miniconda3/envs/sage/include \
  test/cpp/lie_wrapper_driver.cpp common/cpp/lie.cpp \
  -Lcommon/cpp/LiE -L/home/subo-lee/miniconda3/envs/sage/lib \
  -Wl,-rpath,"$PWD/common/cpp/LiE" \
  -Wl,-rpath,/home/subo-lee/miniconda3/envs/sage/lib \
  -llie -lgmpxx -lgmp -o /tmp/lie_wrapper_test
/tmp/lie_wrapper_test
```

The standalone driver tests owning results, virtual characters, exact large
coefficients, error recovery and concurrent callers. Python integration tests
in `test/test_cpp_lie_wrapper.py` compare Adams and tensor operations against
the LiE executable across all seven simple families and exercise cache misses,
timeout recovery, thread concurrency and fork reuse. All test databases are
temporary. See `index/cpp/CHARACTER_CACHE.md` for backend selection.
