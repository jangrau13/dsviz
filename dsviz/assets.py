"""
Where the editor lives, in a checkout and in an install.

The package is used two ways. In this repository it sits beside `web/`, which
is what the test suite and the authoring loop see. Installed from git with
`uv add`, there is no repository around it — a wheel is only what was put
inside it — so that directory is copied into the package itself as `_web/`.

The documentation site is here too, on the same terms: built by `mkdocs
build` from `docs/`, and copied inside the wheel so a student who installs
nothing still gets the whole reference on the port the editor is on.

Starters are not here. They belong to the exercise, in its own `tasks/`, and
the package has none of its own to resolve.

Both layouts are real, so neither is the special case. Each lookup prefers the
bundled copy and falls back to the sibling directory, which means the same code
runs in a student's `.venv` and in this checkout without knowing which it is.
"""

from __future__ import annotations

import pathlib

PACKAGE = pathlib.Path(__file__).resolve().parent
_REPO = PACKAGE.parent


def _resolve(bundled: str, source: str) -> pathlib.Path:
    """The bundled directory if this is an install, the sibling if not."""
    inside = PACKAGE / bundled
    return inside if inside.is_dir() else _REPO / source


def site_dir() -> pathlib.Path:
    """The built documentation site: the full language reference."""
    return _resolve("_site", "site")


def web_dir() -> pathlib.Path:
    """The editor: its page, its stylesheet and its scripts."""
    return _resolve("_web", "web")


def modules_dir() -> pathlib.Path:
    """
    The Python sources the browser fetches.

    Pyodide runs the same modules as CPython, so it reads them straight out of
    the installed package rather than from a copy that could fall behind it.
    """
    return PACKAGE
