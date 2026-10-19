#!/usr/bin/env python3
"""Build index._char_cache_cpp for the Python interpreter running this script.

Example (all generated dependencies remain inside index/cpp/_build):
    python index/cpp/build_char_cache.py --install-deps

GMP/GMPXX and SQLite development headers and libraries must already exist.
Use --gmp-prefix and --sqlite-prefix when they are outside the current Python
environment or the compiler's standard search paths. No compilation occurs
when importing the Python cache module.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile


HERE = Path(__file__).resolve().parent
BUILD = HERE / "_build"
DEPS = BUILD / "deps"
COMMON_CPP = HERE.parents[1] / "common" / "cpp"
LIE = COMMON_CPP / "LiE"


def _include(module_name: str, header: str) -> Path:
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RuntimeError(
            f"Missing build dependency {module_name!r}; rerun with --install-deps "
            "or install index/cpp/requirements-build.txt in this interpreter."
        ) from exc
    include = Path(module.get_include())
    if not (include / header).is_file():
        raise RuntimeError(f"Missing header {include / header}")
    return include


def _library_paths(prefix: str | None, header: str) -> tuple[list[Path], list[Path]]:
    if prefix:
        root = Path(prefix).expanduser().resolve()
        include = root / "include"
        if not (include / header).is_file():
            raise RuntimeError(f"Missing header {include / header}")
        library_dirs = [path for path in (root / "lib", root / "lib64") if path.is_dir()]
        if not library_dirs:
            raise RuntimeError(f"No lib or lib64 directory below {root}")
        return [include], library_dirs
    root = Path(sys.prefix)
    if (root / "include" / header).is_file():
        return _library_paths(str(root), header)
    # Let the compiler use its platform search paths for system packages.
    return [], []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-deps", action="store_true", help="Install pinned header dependencies into _build/deps")
    parser.add_argument("--cxx", default=os.environ.get("CXX", "g++"), help="C++ compiler executable (default: CXX or g++)")
    parser.add_argument("--gmp-prefix", default=os.environ.get("GMP_PREFIX"))
    parser.add_argument("--sqlite-prefix", default=os.environ.get("SQLITE_PREFIX"))
    parser.add_argument("--debug", action="store_true", help="Use -O0 -g instead of -O3")
    args = parser.parse_args(argv)
    BUILD.mkdir(parents=True, exist_ok=True)
    if args.install_deps:
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--no-cache-dir", "--upgrade", "--target", str(DEPS),
            "-r", str(HERE / "requirements-build.txt"),
        ], check=True)
    if DEPS.is_dir():
        sys.path.insert(0, str(DEPS))
        importlib.invalidate_caches()

    try:
        pybind_include = _include("pybind11", "pybind11/pybind11.h")
        json_include = _include("nlohmann_json", "nlohmann/json.hpp")
        gmp_includes, gmp_libs = _library_paths(args.gmp_prefix, "gmpxx.h")
        sqlite_includes, sqlite_libs = _library_paths(args.sqlite_prefix, "sqlite3.h")
    except RuntimeError as exc:
        parser.error(str(exc))
    compiler = shutil.which(args.cxx)
    if compiler is None:
        parser.error(f"C++ compiler {args.cxx!r} was not found")
    subprocess.run(["make", "-C", str(LIE), "-f", "Makefile.shared", "-j4"], check=True)
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if not suffix:
        parser.error("This Python does not define an extension-module suffix")
    output = HERE.parent / f"_char_cache_cpp{suffix}"
    includes = list(dict.fromkeys([
        Path(sysconfig.get_path("include")), Path(sysconfig.get_path("platinclude")),
        pybind_include, json_include, *gmp_includes, *sqlite_includes,
    ]))
    library_dirs = list(dict.fromkeys([*gmp_libs, *sqlite_libs]))
    flags = ["-O0", "-g"] if args.debug else ["-O3", "-DNDEBUG"]
    # A temporary output prevents a failed or interrupted compilation from
    # replacing a previously working extension.
    with tempfile.TemporaryDirectory(prefix="char-cache-", dir=BUILD) as temporary:
        temporary_output = Path(temporary) / output.name
        command = [
            compiler, "-std=c++17", "-shared", "-fPIC", "-pthread", "-fvisibility=hidden",
            *flags, *shlex.split(os.environ.get("CHAR_CACHE_CXXFLAGS", "")),
            *[f"-I{directory}" for directory in includes],
            str(HERE / "char_decomposition_cache.cpp"),
            str(HERE / "char_decomposition_bindings.cpp"),
            str(HERE / "index_expand.cpp"),
            str(HERE / "index_singlet_project.cpp"),
            str(COMMON_CPP / "lie.cpp"),
            "-o", str(temporary_output),
            *[f"-L{directory}" for directory in library_dirs],
            *[f"-Wl,-rpath,{directory}" for directory in library_dirs],
            f"-L{LIE}", "-Wl,-rpath,$ORIGIN/../common/cpp/LiE",
            "-llie", "-lgmpxx", "-lgmp", "-lsqlite3",
            *shlex.split(os.environ.get("CHAR_CACHE_LDFLAGS", "")),
        ]
        if sys.platform == "darwin":
            command.extend(["-undefined", "dynamic_lookup"])
        print(shlex.join(command), flush=True)
        subprocess.run(command, check=True)
        temporary_output.replace(output)
    metadata = {
        "python": sys.executable, "python_version": sys.version,
        "compiler": compiler, "output": str(output), "command": command,
        "requirements": (HERE / "requirements-build.txt").read_text(),
    }
    (BUILD / "last_build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Built {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
