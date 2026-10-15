# LiE shared library

LiE and this project's native integration use LGPL-2.1-or-later. The project-root
[LICENSE](../../../LICENSE) includes the full license text, scope, upstream
attribution and permission notices.

From the project root:

```sh
make -C common/cpp/LiE -f Makefile.shared -j4
```

This builds `common/cpp/LiE/liblie.so` with the system C compiler (`gcc`),
`-O3` and position-independent code. LiE is written in C; the resulting
library can be linked from C or through a C interface in C++.
Override `CC`, `CFLAGS`, `CPPFLAGS`, `LDFLAGS`, `LDLIBS`, or `BISON` on the make
command line when needed. GNU Make 4.3 or newer is required for the grouped
parser-generation target.

The build includes the mathematical routines, runtime and interpreter,
with readline disabled. It retains the runtime globals from `main.c` and
renames its executable entry point to `lie_cli_main`. Loading the library
does not run that entry point. Exported functions include `Adams`, `Tensor`
and `init`. The shared-library link rejects unresolved references; the only
external runtime dependency is libc on the tested Linux system (including
pthread functions on this glibc version).

Generated parser files, dependency files and PIC objects live under
`_build/shared/`. The original executable Makefile is preserved. Small hooks
in the downloaded sources provide allocation tracking, error recovery,
cooperative timeout checks and reset of cached runtime pointers. They are
enabled only by this build's `N2_LIE_EMBEDDABLE` definition; the original
executable build retains its usual behavior. Build artifacts and the optional
local build tool are ignored by Git.

Bison is required to generate `parser.c` and `parser.h`. The build uses a
system Bison when available. On this machine, Ubuntu's Bison 3.8.2 package
was downloaded and extracted under `_build/tools/` without a system install;
the Makefile automatically finds that copy and its parser skeleton data.
On another machine, install Bison or pass `BISON=/path/to/bison` and, if
relocated, its `BISON_PKGDATADIR` environment variable.

For native callers, use `common/cpp/lie.hpp` and compile `common/cpp/lie.cpp`.
Link `-L/path/to/common/cpp/LiE -llie -lgmpxx -lgmp -pthread`, with a runtime
library search path (for example, `-Wl,-rpath,/path/to/common/cpp/LiE`).
The wrapper and its private `lie_bridge.c` adapter initialize the mathematical
core, own its memory, serialize its global state, and recover errors inside C.
Do not mix raw upstream API calls or interpreter initialization with this
wrapper. See `../LIE_WRAPPER.md` for the supported C++ interface.

Validation on this machine: `ldd -r` resolves every runtime reference,
the library has no text relocations, and a linked C caller directly computed
the following four identities in one process:

- `Adams(2, [1], A1) = -[0] + [2]`
- `[1] tensor [1]` in A1 is `[0] + [2]`
- `Adams(2, [1,0], A2) = -[0,1] + [2,0]`
- `[1,0] tensor [1,0]` in A2 is `[0,1] + [2,0]`
