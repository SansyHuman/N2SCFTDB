"""Load the explicitly built native index-calculation extension.

Set ``N2SCFTDB_CHAR_CACHE_BACKEND`` to ``python`` to use the reference
implementation, ``cpp`` to require the extension, or ``auto`` (the default)
to use it when installed. Full-index calculations use ``require_index=True``
to require expansion/projection support independently of this cache setting.
Loading never downloads or compiles code.
"""

from __future__ import annotations

from importlib import import_module
import os
from types import ModuleType


_MODULE_NAME = f"{__package__}._index_calculation_cpp"
_INDEX_ERROR = (
    "The native index backend has not been built for this Python, or is outdated. "
    "Run this interpreter with index/cpp/build_index_calculation.py --install-deps, "
    "or select backend='python' for FORM."
)


def load_native(*, require_index: bool = False) -> ModuleType | None:
    """Select cache support or require the full native index implementation.

    Only an absent extension permits automatic cache fallback. Missing
    dependencies and unusable binaries always retain their original errors.
    ``require_index`` ignores the character-cache environment setting and
    raises RuntimeError if the extension is absent or lacks expand_and_project.
    """
    backend = ("cpp" if require_index else
               os.environ.get("N2SCFTDB_CHAR_CACHE_BACKEND", "auto").lower().strip())
    if backend not in {"auto", "python", "cpp"}:
        raise ValueError(
            "N2SCFTDB_CHAR_CACHE_BACKEND must be 'auto', 'python', or 'cpp'"
        )
    if backend == "python":
        return None
    try:
        native = import_module(_MODULE_NAME)
    except ModuleNotFoundError as exc:
        # A missing dependency or an unusable binary must remain visible.
        if exc.name != _MODULE_NAME:
            raise
        if require_index:
            raise RuntimeError(_INDEX_ERROR) from exc
        if backend == "auto":
            return None
        raise ImportError(
            "The C++ character-cache backend has not been built for this Python. "
            "Run this interpreter with index/cpp/build_index_calculation.py --install-deps."
        ) from exc
    if require_index and not hasattr(native, "expand_and_project"):
        raise RuntimeError(_INDEX_ERROR)
    return native
