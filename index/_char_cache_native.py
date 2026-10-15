"""Select the optional, explicitly built character-cache extension.

Set ``N2SCFTDB_CHAR_CACHE_BACKEND`` to ``python`` to use the reference
implementation, ``cpp`` to require the extension, or ``auto`` (the default)
to use it when installed. Importing the cache never downloads or compiles code.
"""

from __future__ import annotations

from importlib import import_module
import os
from types import ModuleType


def load_native() -> ModuleType | None:
    """Load the requested backend; only an absent extension permits fallback."""
    backend = os.environ.get("N2SCFTDB_CHAR_CACHE_BACKEND", "auto").lower().strip()
    if backend not in {"auto", "python", "cpp"}:
        raise ValueError(
            "N2SCFTDB_CHAR_CACHE_BACKEND must be 'auto', 'python', or 'cpp'"
        )
    if backend == "python":
        return None
    module_name = f"{__package__}._char_cache_cpp"
    try:
        return import_module(module_name)
    except ModuleNotFoundError as exc:
        # A missing dependency or an unusable binary must remain visible.
        if exc.name != module_name:
            raise
        if backend == "auto":
            return None
        raise ImportError(
            "The C++ character-cache backend has not been built for this Python. "
            "Run this interpreter with index/cpp/build_char_cache.py --install-deps."
        ) from exc
