# Project-Specific Pitfalls

Real failure patterns caught in code review on this codebase. Each has a before/after example.

## Ghost Assertions — Test That Never Fails

A conditional check with `pass` asserts nothing. It will always pass even if the feature is
completely broken.

```python
# Wrong — never fails regardless of what prior contains
api_entry = prior.get(fp)
if api_entry is not None:
    pass  # "structure confirmed by other test"

# Right — assert the specific value
assert fp in prior, "fingerprint missing from build_prior result"
assert prior[fp]["sentinel_state"] == "auto_resolved", (
    f"expected 'auto_resolved', got {prior[fp].get('sentinel_state')!r}"
)
```

## Incomplete State Machine Coverage

List all paths explicitly. Comment each test group with its path number so gaps are obvious.
Test API failure variants separately — do not bundle them with success paths.

```python
# ---------------------------------------------------------------------------
# Path 8: CODE_REMOVED → REPLY_FAILED — hunk deleted but resolve API fails
# ---------------------------------------------------------------------------
def test_path_code_removed_reply_failed():
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = False  # API failure
    ...
```

A state machine with N terminal outcomes needs at least N tests, plus one per API failure
variant. If the code path for "resolve API returns False" exists, test it in its own function.

## `assert_called_once()` Without Argument Assertions

Passes even when the arguments are wrong. For methods where the body content matters
(sentinels, fingerprints, guidance text) — always verify what was passed, not just that
the call was made.

```python
# Wrong — passes even if the wrong sentinel is in the body
adapter.resolve_inline_comment.assert_called_once()

# Right — verify the sentinel and fingerprint are actually present
adapter.resolve_inline_comment.assert_called_once()
resolve_body = adapter.resolve_inline_comment.call_args[1]["reply_body"]
assert "revue:state=auto_resolved" in resolve_body
assert "fp-4" in resolve_body
```

Same rule as AC contract testing: assert every field the caller is supposed to set.

## Mock Return Type Mismatch

Mock returns wrong type — e.g. bare `str` instead of `ResolutionResult`. Tests pass but
production code breaks when it accesses `.verdict` or `.guidance` on a string.

```python
# Wrong — str masquerading as ResolutionResult
strategy.resolve.return_value = "fully"

# Right — actual dataclass matching the Protocol
strategy.resolve.return_value = ResolutionResult(
    verdict=ResolutionVerdict.FULLY,
    guidance="Issue addressed.",
)
```

Always import and construct the real return type. If the Protocol changes, the import breaks
immediately rather than silently passing with the wrong type.

## Unrealistic Test Data

Placeholders like `"fp-terminal"` won't hit format-dependent validation paths that real
data would reach. Use data that matches production format.

```python
fp = "fp-terminal"       # Wrong — not a real fingerprint; bypasses format validation
fp = "abcd1234efab5678"  # Right — 16-char hex matches production fingerprint format
```
