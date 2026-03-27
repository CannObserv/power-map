# Design: Cache FallbackAddressNormalizer as module-level singleton

**Issue:** #45
**Date:** 2026-03-27

## Goal

Eliminate per-request construction of `FallbackAddressNormalizer` in `orgs_addresses.py`. Config is immutable per process; the instance should be built once at import time.

## Approved approach

Keep `_build_normalizer()` as a private initializer; call it once at module level:

```python
_NORMALIZER: FallbackAddressNormalizer = _build_normalizer()
```

Replace the call site in `_maybe_confirm()`:

```python
result = await _NORMALIZER.normalize(raw)
```

## Key decisions

- **Keep `_build_normalizer()`** — readable helper, signals where the singleton comes from. Alternative (inline at module level) saves a function but reduces clarity.
- **Patch `_NORMALIZER` in tests** — switch all 6 `@patch("...FallbackAddressNormalizer")` decorators to `@patch("src.api.admin.orgs_addresses._NORMALIZER")`, passing a pre-configured `AsyncMock` directly. Simpler than the current `mock_cls.return_value = inst` boilerplate.
- **`os` import** — remove if no longer used after the refactor.

## Out of scope

- Thread safety / process-local caching concerns (env reads are synchronous and immutable)
- Sharing the singleton across other modules
- Hot-reloading config without restart (not a requirement)
