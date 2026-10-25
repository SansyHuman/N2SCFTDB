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

For concurrent worker threads, give each worker an environment:

```cpp
lie::Environment environment;  // keep for the worker's lifetime
auto psi2 = environment.adams("A1", 2, lie::Labels{1});
auto product = environment.tensor("A2", lie::Labels{1, 0}, lie::Labels{0, 1});
```

`Environment` owns an isolated copy of `liblie.so`, loaded once from a sealed
Linux anonymous memory file (`memfd_create`) with `dlopen(RTLD_NOW | RTLD_LOCAL)`.
The build hides all internal LiE functions and global variables using
`LiE/exports.map`, so each copy binds to its own state. This does not use
`dlmopen` namespaces and supports 32 or more environments, subject to memory
and file-descriptor limits. Linux with `/proc/self/fd` and executable memfd
mappings is required; loading failure raises an exception with no silent
fallback to a shared runtime. No library copies are retained on disk.

Each environment has its own mutex, allocation tracking, object limit,
deadline and C recovery frame. Separate environments calculate concurrently;
calls sharing one environment serialize. The original free functions continue
to use the original serialized library instance for compatibility. Returned
C++ values own their memory and survive environment destruction. Bridge output
is always freed through the same instance that allocated it. Destruction must
not overlap calls on that environment. Each library registers fork handlers for
its mutex; unloading removes those registrations on the supported glibc host.
All per-call mathematical allocations are freed even on failure, and `longjmp`
recovery stays inside C without skipping C++ destructors.

The character cache owns an environment lazily, creating it only on the first
native calculation. Cache hits need no environment. It reuses the environment
until `close()` or destruction, and discards inherited cache/runtime state after
fork. Separate cache instances can therefore execute LiE simultaneously. This
does not itself change the serial `project_index` or batch-cache schedulers.
Do not initialize the LiE interpreter alongside native calls in one instance.

Supported groups are finite simple A, B, C, D, E, F and G types at their valid
ranks. Labels are nonnegative machine integers. To leave room for upstream
signed-long weight arithmetic, the wrapper conservatively bounds the sum of
labels times the Adams power by
`floor((sqrt(LONG_MAX)-1)/(32*(rank+1)^2))`; tensor inputs each use half that
bound. Coefficients use exact GMP integers at the interface, subject to LiE's
own bigint storage limits (signed-short digit counts, at most 32,767 base-2^15
digits). This is not a replacement for arbitrary-rank/weight symbolic algebra.

Build `liblie.so` using `LiE/Makefile.shared`, then compile `lie.cpp` with the
caller and link LiE, GMPXX, GMP and libdl. With this machine's dependencies:

```sh
make -C common/cpp/LiE -f Makefile.shared -j4
g++ -std=c++17 -O2 -pthread \
  -I/home/subo-lee/miniconda3/envs/sage/include \
  test/cpp/lie_wrapper_driver.cpp common/cpp/lie.cpp \
  -Lcommon/cpp/LiE -L/home/subo-lee/miniconda3/envs/sage/lib \
  -Wl,-rpath,"$PWD/common/cpp/LiE" \
  -Wl,-rpath,/home/subo-lee/miniconda3/envs/sage/lib \
  -llie -lgmpxx -lgmp -ldl -o /tmp/lie_wrapper_test
/tmp/lie_wrapper_test
```

The standalone driver tests owning results, virtual characters, exact large
coefficients, error recovery and concurrent callers. Python integration tests
in `test/test_cpp_lie_wrapper.py` compare Adams and tensor operations against
the LiE executable across all seven simple families and exercise cache misses,
timeout recovery, 32 live independent environments, hidden symbols, lazy
loading, load/unload cleanup and inherited-cache fork reuse. All test databases
are temporary. See `index/cpp/CHARACTER_CACHE.md` for backend selection.

`test/benchmark_lie_environments.py` measures fixed uncached SU(9) and E8
workloads with 1–32 threads, one native cache per worker, exact comparisons,
separate startup timings and resident memory measurements.
