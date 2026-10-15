# Native character decomposition cache

This native integration is licensed under LGPL-2.1-or-later. The project-root
[LICENSE](../../LICENSE) lists the covered files and includes the complete
license text and upstream LiE copyright and permission notices.

`char_decomposition_cache.hpp` and `.cpp` implement the character-cache core in
C++17, in namespace `n2scftdb::characters`. It uses GMP, SQLite3 and
nlohmann/json, and calls the native LiE wrapper in `common/cpp/lie.hpp` on
cache misses. Adams and tensor results are transferred as exact native
objects, without generating commands or parsing LiE output.
`char_decomposition_bindings.cpp` exposes the same core through pybind11.

The native class supports cached Adams decompositions, reuse of existing
composite factors, products of virtual characters, and exact singlet pairing.
Complete products are split before projection so the final tensor product does
not need a full irreducible decomposition. Coefficients are GMP integers;
Dynkin labels and Adams exponents use checked signed 64-bit integers.

## Build for Python

Run the build with the interpreter that will import the extension. On this
machine, Sage's environment supplies the GMP and SQLite development files:

```sh
/home/subo-lee/miniconda3/envs/sage/bin/python \
  index/cpp/build_char_cache.py --install-deps --cxx /usr/bin/g++
```

`--install-deps` installs the pinned pybind11 and nlohmann/json header packages
from `requirements-build.txt` into the ignored `index/cpp/_build/deps`
directory. It does not modify the Python environment. Subsequent builds can
omit that option. The build accepts `--gmp-prefix`, `--sqlite-prefix`, `CXX`,
`GMP_PREFIX`, `SQLITE_PREFIX`, `CHAR_CACHE_CXXFLAGS` and `CHAR_CACHE_LDFLAGS`.
It builds `common/cpp/LiE/liblie.so`, compiles `common/cpp/lie.cpp`, links
LiE, GMP/GMPXX and SQLite3, and writes an extension with the current Python
ABI suffix under `index/`. Compilation uses a temporary output and replaces
the extension only after success. Generated binaries are not checked in.
The extension locates `liblie.so` relative to its own directory. GCC and Bison
are required for LiE; see `common/cpp/LiE/SHARED_BUILD.md` for the local Bison
fallback used on this machine.

The native engine needs no LiE executable on `PATH`. The optional subprocess
implementation uses POSIX APIs and requires a LiE executable on `PATH`, or
selected with `lie_executable`. Process execution uses pipes without a shell.

## Compatible Python API

Existing imports and calls continue to work:

```python
from index.char_decomposition_cache import CharacterDecompositionCache

with CharacterDecompositionCache(database_path="my-character-cache.db") as cache:
    print(cache.backend)  # "cpp" after a successful build, otherwise "python"
    print(cache.lie_backend)  # "native" with the default C++ configuration
    decomposition = cache.get_decomposition("A1", (1,), (0, 1))
    assert decomposition == {(0,): -1, (2,): 1}
```

Set `N2SCFTDB_CHAR_CACHE_BACKEND` before importing the module:

- `auto` (default) loads the extension when available and otherwise uses Python.
- `cpp` requires the extension and reports a missing build explicitly.
- `python` selects the original Python implementation.

Importing never downloads or compiles dependencies. A broken installed native
extension raises its import error rather than silently switching backends.

The independent `lie_backend` constructor/builder option selects the LiE engine:

- `auto` (default) uses native LiE with the C++ backend and the default
  `lie_executable="lie"`. A custom executable selects subprocess mode to
  preserve existing executable wrappers. The Python backend uses subprocesses.
- `native` explicitly requires the C++ extension and ignores the executable.
- `subprocess` explicitly selects the existing executable interface.

The CLI accepts `--lie-backend auto|native|subprocess`. Native LiE is serialized
within each process because upstream uses global state. Existing Python process
pools still run calculations in parallel across processes. Native `timeout` is
a cooperative calculation deadline sampled adaptively during allocation and
Weyl enumeration, with mandatory checks at calculation boundaries; lock
waiting is excluded. `max_objects` limits live LiE objects;
`max_nodes` applies only to subprocess interpreter mode. The standalone wrapper
and its weight limits are documented in `common/cpp/LIE_WRAPPER.md`.

The Python class delegates SQLite lookups/writes, JSON decomposition encoding,
Adams-product canonicalization/splitting, duality and exact singlet contraction,
native LiE Adams/tensor calculations, and optional LiE subprocess execution to C++.
Python retains dependency scheduling, batching, process pools, progress
callbacks, CLI handling, exact input conversion, and overridable calculation
hooks. The full native class is separately exposed as
`index._char_cache_cpp.CharacterDecompositionCache` for direct native calls.
The `_run_lie` hook and polynomial formatting/parsing are used only by
subprocess mode. `_calculate_decomposition` and `_tensor_decompositions` remain
the Python calculation hooks in either mode.

`build_decomposition_cache` retains its Python enumeration and order barriers.
Its worker processes use the selected backend and return completed values to
the parent for persistence. The native class's own `get_decompositions` is a
serial batch API; Python's `max_workers` and builder `processes` options retain
the existing parallel orchestration.

## Direct C++ use

The core does not depend on Python or pybind11:

```cpp
#include "char_decomposition_cache.hpp"

namespace chars = n2scftdb::characters;
chars::CharacterDecompositionCache cache("my-character-cache.db");
auto decomposition = cache.get_decomposition("A1", {1}, {0, 1});
// decomposition: {(0): -1, (2): 1}

chars::Product product{{{1}, {2}}};
auto singlets = cache.get_singlet_multiplicities("A1", 1, {product});
// singlets: {1}, the singlet in the fundamental tensor square
```

Compile `char_decomposition_cache.cpp` and `common/cpp/lie.cpp` with C++17 and
`-pthread`, make GMP, SQLite and nlohmann/json headers available, and link
`-llie -lgmpxx -lgmp -lsqlite3` with the LiE library directory and runtime path.
The Python build script's printed command shows the include and library paths
detected for this machine; omit the binding source when building a native caller.

## Storage and ownership

Python and C++ share schema version 1 in `char_decomposition_cache.db`:
`character_decompositions` and `singlet_coefficients`. Keys use the same compact
JSON and weighted Adams padding. Coefficients remain decimal strings, including
negative values and values larger than 64 bits. Empty decompositions and zero
singlet coefficients are successful cache hits. Failed LiE calls are not saved
as zero results.

The Python wrapper creates one native client per thread and process. A direct
C++ or native-extension client belongs to one thread; use separate instances
for concurrent callers. Independent clients can share the same SQLite file.
Connections open lazily, use WAL and a busy timeout, and keep write transactions
short. LiE runs outside write transactions. `close()` releases the calling
thread's Python and native state; later calls can reopen the cache.

## Tests

All cache tests use temporary databases. With Sage's `bin` directory on `PATH`:

```sh
N2SCFTDB_CHAR_CACHE_BACKEND=cpp sage -python -B -m unittest \
  test.test_character_decomposition_cache \
  test.test_cached_decomposition_planning \
  test.test_build_decomposition_cache \
  test.test_singlet_projection \
  test.test_cpp_char_decomposition_cache test.test_cpp_lie_wrapper -v
```

Repeat the existing four suites with `N2SCFTDB_CHAR_CACHE_BACKEND=python` to
exercise the reference implementation. Native integration tests additionally
check Python/C++ cache interoperability, exact large coefficients, LiE failures
and timeouts, duality conventions, and concurrent independent clients.
