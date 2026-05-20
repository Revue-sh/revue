---
name: unit-test-principles
description: Unit testing standards for Revue. Load before writing any unit tests to apply the 5 quality principles — naming, AAA structure, behaviour not implementation, isolation, and realistic data. Also provides a pre-completion checklist and project-specific pitfall reference.
---

# Unit Test Principles

Apply all 5 principles to every test you write. See `references/pitfalls.md` for project-specific
anti-patterns with before/after examples.

## 1. Lean and Accurate

Test essential business logic and the paths that matter — not coverage numbers.
Every test must fail if the behaviour it describes breaks.

> If a test takes more than 30 seconds to read and understand, rewrite it.

## 2. Test Behaviour, Not Implementation

Assert on return values and what the unit does to its collaborators. Never assert on private state.

```python
# Wrong — asserts internal state
assert tracker._state == HunkState.CHANGED

# Right — asserts observable outcome and side-effect
assert result == HunkState.RESOLVE_REPLY_POSTED
adapter.resolve_inline_comment.assert_called_once()
```

No assertions on `_private_attr`, internal arrays, or intermediate variables.
If you need to verify internal state, expose it through a public method first.

## 3. Name and Structure Tests with AAA

**Naming — every test name must answer:**

1. What is being tested
2. Under what condition
3. Expected outcome

```python
# Wrong — says nothing
def test_build_prior():

# Right — self-documenting
def test_build_prior_encodes_sentinel_state_in_entry():
```

**Structure — Arrange / Act / Assert, labelled with comments. One Act line. One Assert group.**

```python
def test_path_code_removed_auto_resolves_without_nova():
    # Arrange
    adapter = MagicMock()
    adapter.resolve_inline_comment.return_value = True
    tracker = HunkTracker(
        adapter=adapter, dedup_store=MagicMock(), resolution_strategy=MagicMock()
    )
    diff = "@@ -8,5 +8,0 @@ def foo():\n-    x = 1\n-    y = 2\n"

    # Act
    result = tracker.resolution_status("fp-4", _prior(line_number=10), new_diff=diff)

    # Assert — state machine outcome
    assert result == HunkState.RESOLVE_REPLY_POSTED
    # Assert — Nova not consulted (code removal is self-evident)
    tracker._resolution_strategy.resolve.assert_not_called()
```

## 4. Deterministic and Isolated

Each test must be fully independent — no shared state, no order dependency, no side effects
between tests.

**Mock return types must match production contracts.** If production returns a typed dataclass,
the mock must return that same type — not a bare string or dict.

```python
# Wrong — bare str, not the actual Protocol return type
strategy.resolve.return_value = "fully"

# Right — matches the actual Protocol contract
strategy.resolve.return_value = ResolutionResult(
    verdict=ResolutionVerdict.FULLY,
    guidance="Issue addressed.",
)
```

## 5. Realistic Test Data

Use fake-but-realistic data. Placeholder strings (`"abc"`, `"fp-terminal"`, `1`) mask
validation bugs that only surface with real-format inputs.

```python
fp = "abcd1234efab5678"  # 16-char hex — matches production fingerprint format
```

---

## Pre-Completion Checklist

Run through this before marking any test task complete:

- [ ] Test name states: subject + condition + expected outcome
- [ ] AAA structure visible and labelled with comments
- [ ] No assertions on `_private_attr` or internal state
- [ ] Mock return types match production contracts (not bare strings or dicts)
- [ ] No ghost assertions — no `if x: pass` blocks that always succeed
- [ ] Every `assert_called_once()` paired with argument assertions (body, sentinel, IDs)
- [ ] Test data is realistic, not placeholder strings
- [ ] State machines: every path including API failure variants has a dedicated test

## References

Load `references/pitfalls.md` for before/after examples of each pitfall, including the
specific patterns that have shipped broken code in this project.
