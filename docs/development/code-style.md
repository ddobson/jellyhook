# Code Style

Enforced by ruff (`ruff.toml`) and mypy (`mypy.ini`); see [setup.md](setup.md) for the
commands.

- Python 3.13+.
- Strict typing, with zero mypy errors (CI enforces it). Tests are checked too, but `mypy.ini` does
  not require annotations on test functions.
- Modern type hints: `list[str]`, not `typing.List`; `str | None`, not `typing.Optional`.
- Line length: 100 characters.
- Double quotes for strings.
- Imports sorted with isort (combine as-imports, first-party first).
- Google-style docstrings with types in the docstring.
- PEP8 naming, and the most specific exception type that fits.
- Every new piece of functionality gets unit tests. Tests are plain pytest functions (no test
  classes) that use fixtures for setup. Workflows that span tools also get component tests.
