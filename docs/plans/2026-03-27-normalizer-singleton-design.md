# Design: Cache FallbackAddressNormalizer as module-level singleton

**Issue:** #45
**Date:** 2026-03-27

## Goal

Eliminate per-request construction of `FallbackAddressNormalizer` in `orgs_addresses.py`. Config is immutable per process; the instance should be built once at import time.

## Approved approach

Use `_init_normalizer()` as a private one-time initializer; call it once at module level, then delete it:

```python
_NORMALIZER: FallbackAddressNormalizer = _init_normalizer()
del _init_normalizer  # prevent accidental re-invocation after module init
```

Replace the call site in `_maybe_confirm()`:

```python
result = await _NORMALIZER.normalize(raw)
```

## Key decisions

- **Rename `_build_normalizer` → `_init_normalizer`** — "init" signals it is a one-time setup, not a factory.
- **`del _init_normalizer` after assignment** — removes the name from the module namespace; any accidental re-call raises `AttributeError` immediately.
- **Patch `_NORMALIZER` in tests** — switch all 6 `@patch("...FallbackAddressNormalizer")` decorators to `@patch("src.api.admin.orgs_addresses._NORMALIZER")`, passing a pre-configured `AsyncMock` directly. Simpler than the current `mock_cls.return_value = inst` boilerplate.
- **`os` import** — keep; still used inside `_init_normalizer()`.

## Out of scope

- Thread safety / process-local caching concerns (env reads are synchronous and immutable)
- Sharing the singleton across other modules
- Hot-reloading config without restart (not a requirement)
