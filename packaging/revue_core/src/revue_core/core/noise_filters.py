"""
Noise filters — suppress false positives (Story [008]).

SRP: filtering only.
OCP: filters are pluggable NoiseFilter Protocol implementations.

Built-in filters
----------------
Generic:
  LowConfidenceFilter     — suppress findings below a confidence threshold
  TestFileFilter          — suppress non-critical findings in test files
  GeneratedFileFilter     — suppress all findings in auto-generated files
  EmptyIssueFilter        — suppress findings with blank issue text

Language-aware DI pattern filters (AC Story 14):
  SwiftDIFilter           — suppress Swinject DI container noise (Swift)
  KotlinDIFilter          — suppress Koin DI container noise (Kotlin)
  JavaDIFilter            — suppress Dagger DI container noise (Java)
  TypeScriptDIFilter      — suppress Angular/NestJS DI noise (TypeScript)

Linter suppression filters (AC Story 14):
  LinterSuppressionFilter — suppress findings where the source line has a
                            linter-disable comment (SwiftLint, Detekt,
                            Checkstyle, ESLint)

Configurable via .revue.yml:
  noise_filters:
    disable: []                 # list of filter names to disable
    low_confidence_threshold: 0.5
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from .models import AIReview
from .diff_parser import detect_language


class NoiseFilter(Protocol):
    """Pluggable noise filter (OCP — add filters without modifying this file)."""
    name: str
    def should_suppress(self, review: AIReview) -> bool: ...


@dataclass
class FilterResult:
    kept: list[AIReview]
    suppressed: list[tuple[AIReview, str]]  # (review, filter_name)

    @property
    def suppressed_count(self) -> int:
        return len(self.suppressed)

    @property
    def kept_count(self) -> int:
        return len(self.kept)


class LowConfidenceFilter:
    """Suppress findings below a confidence threshold."""
    name = "low-confidence"

    def __init__(self, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def should_suppress(self, review: AIReview) -> bool:
        return review.confidence < self.threshold


class TestFileFilter:
    """Suppress non-critical findings in test files."""
    name = "test-file"
    _TEST_PATTERNS = ("test_", "_test.", "spec_", "_spec.", "/tests/", "/test/")

    def should_suppress(self, review: AIReview) -> bool:
        path = review.file_path.lower()
        if review.severity == "high":
            return False
        return any(pattern in path for pattern in self._TEST_PATTERNS)


class GeneratedFileFilter:
    """Suppress all findings in auto-generated files."""
    name = "generated-file"
    _GENERATED_PATTERNS = (
        ".min.js", ".min.css", "package-lock.json", "yarn.lock",
        ".pb.go", "_pb2.py", ".generated.", "/__generated__/",
    )

    def should_suppress(self, review: AIReview) -> bool:
        path = review.file_path.lower()
        return any(pattern in path for pattern in self._GENERATED_PATTERNS)


class EmptyIssueFilter:
    """Suppress findings with blank issue text."""
    name = "empty-issue"

    def should_suppress(self, review: AIReview) -> bool:
        return not review.issue.strip()


# ---------------------------------------------------------------------------
# Language-aware DI pattern filters (AC Story 14)
# ---------------------------------------------------------------------------

class SwiftDIFilter:
    """Suppress Swinject DI container noise in Swift files.

    Swinject registers dependencies with `container.register(...)` and
    resolves them with `container.resolve(...)`. AI agents sometimes flag
    these as "unused variable" or "force unwrap" false positives.
    """
    name = "swift-di"
    _DI_PATTERNS = (
        re.compile(r"container\.(register|resolve|registerSingleton)\s*[(<]", re.IGNORECASE),
        re.compile(r"Assembler\s*\(", re.IGNORECASE),
        re.compile(r"@\s*Injected\s*\(", re.IGNORECASE),
    )

    def should_suppress(self, review: AIReview) -> bool:
        if detect_language(review.file_path) != "swift":
            return False
        text = f"{review.issue} {review.suggestion}".lower()
        return any(p.search(text) or p.search(review.file_path) for p in self._DI_PATTERNS)


class KotlinDIFilter:
    """Suppress Koin DI container noise in Kotlin files.

    Koin uses `module { single { ... } }` and `by inject()` / `by viewModel()`.
    """
    name = "kotlin-di"
    _DI_PATTERNS = (
        re.compile(r"\bmodule\s*\{", re.IGNORECASE),
        re.compile(r"\b(single|factory|viewModel|scoped)\s*\{", re.IGNORECASE),
        re.compile(r"\bby\s+(inject|viewModel|get)\s*\(", re.IGNORECASE),
        re.compile(r"\bgetKoin\s*\(\s*\)", re.IGNORECASE),
    )

    def should_suppress(self, review: AIReview) -> bool:
        if detect_language(review.file_path) != "kotlin":
            return False
        text = f"{review.issue} {review.suggestion}".lower()
        return any(p.search(text) for p in self._DI_PATTERNS)


class JavaDIFilter:
    """Suppress Dagger DI annotations in Java files.

    Dagger uses @Inject, @Module, @Component, @Provides, @Binds.
    """
    name = "java-di"
    _DI_ANNOTATIONS = (
        re.compile(r"@\s*(Inject|Module|Component|Provides|Binds|Subcomponent)", re.IGNORECASE),
        re.compile(r"\bDaggerComponent\b"),
    )

    def should_suppress(self, review: AIReview) -> bool:
        if detect_language(review.file_path) != "java":
            return False
        text = f"{review.issue} {review.suggestion}"
        return any(p.search(text) for p in self._DI_ANNOTATIONS)


class TypeScriptDIFilter:
    """Suppress Angular/NestJS DI decorator noise in TypeScript files.

    Angular/NestJS use @Injectable(), @Inject(), @Component({providers:[...]}).
    """
    name = "typescript-di"
    _DI_PATTERNS = (
        re.compile(r"@\s*(Injectable|Inject|Component|NgModule|Controller|Module)\s*[({(]",
                   re.IGNORECASE),
        re.compile(r"\bproviders\s*:\s*\[", re.IGNORECASE),
    )

    def should_suppress(self, review: AIReview) -> bool:
        if detect_language(review.file_path) not in ("typescript", "javascript"):
            return False
        text = f"{review.issue} {review.suggestion}"
        return any(p.search(text) for p in self._DI_PATTERNS)


# ---------------------------------------------------------------------------
# Linter suppression comment filter (AC Story 14)
# ---------------------------------------------------------------------------

class LinterSuppressionFilter:
    """Suppress findings where the finding description references a linter-disable comment.

    Covers:
      Swift:      // swiftlint:disable <rule>
      Kotlin:     @Suppress("rule") / // detekt:disable
      Java:       // CHECKSTYLE:OFF / @SuppressWarnings
      TypeScript: // eslint-disable[-next-line] <rule>
      Python:     # noqa / # type: ignore
    """
    name = "linter-suppression"
    _SUPPRESSION_PATTERNS = (
        # SwiftLint
        re.compile(r"swiftlint\s*:\s*(disable|ignore)", re.IGNORECASE),
        # Detekt (Kotlin)
        re.compile(r"detekt\s*:\s*disable", re.IGNORECASE),
        re.compile(r'@Suppress\s*\(', re.IGNORECASE),
        # Checkstyle (Java)
        re.compile(r"CHECKSTYLE\s*:\s*(OFF|SUPPRESS)", re.IGNORECASE),
        re.compile(r"@SuppressWarnings\s*\(", re.IGNORECASE),
        # ESLint (TypeScript/JavaScript)
        re.compile(r"eslint-disable(-next-line)?", re.IGNORECASE),
        # Python
        re.compile(r"#\s*noqa", re.IGNORECASE),
        re.compile(r"#\s*type\s*:\s*ignore", re.IGNORECASE),
    )

    def should_suppress(self, review: AIReview) -> bool:
        text = f"{review.issue} {review.suggestion}"
        return any(p.search(text) for p in self._SUPPRESSION_PATTERNS)


# ---------------------------------------------------------------------------
# Default filter set and public API
# ---------------------------------------------------------------------------

_DEFAULT_FILTERS: list[NoiseFilter] = [
    LowConfidenceFilter(threshold=0.5),
    TestFileFilter(),
    GeneratedFileFilter(),
    EmptyIssueFilter(),
    # Language-aware DI filters
    SwiftDIFilter(),
    KotlinDIFilter(),
    JavaDIFilter(),
    TypeScriptDIFilter(),
    # Linter suppression
    LinterSuppressionFilter(),
]


def build_filters(
    disabled: list[str] | None = None,
    low_confidence_threshold: float = 0.5,
) -> list[NoiseFilter]:
    """Build the active filter list from config.

    Args:
        disabled: list of filter names to disable (from .revue.yml noise_filters.disable)
        low_confidence_threshold: threshold for LowConfidenceFilter
            (from .revue.yml noise_filters.low_confidence_threshold)

    Returns:
        List of active NoiseFilter instances.
    """
    disabled_set = set(disabled or [])
    filters: list[NoiseFilter] = [
        LowConfidenceFilter(threshold=low_confidence_threshold),
        TestFileFilter(),
        GeneratedFileFilter(),
        EmptyIssueFilter(),
        SwiftDIFilter(),
        KotlinDIFilter(),
        JavaDIFilter(),
        TypeScriptDIFilter(),
        LinterSuppressionFilter(),
    ]
    return [f for f in filters if f.name not in disabled_set]


def apply_noise_filters(
    findings: list[AIReview],
    filters: list[NoiseFilter] | None = None,
) -> FilterResult:
    """
    Apply noise filters to a list of findings.

    - Each finding is tested against all filters
    - First matching filter suppresses the finding (short-circuit)
    - Returns FilterResult with kept and suppressed lists
    - Never raises
    """
    active = filters if filters is not None else _DEFAULT_FILTERS
    kept: list[AIReview] = []
    suppressed: list[tuple[AIReview, str]] = []

    for review in findings:
        suppressed_by: str | None = None
        for f in active:
            try:
                if f.should_suppress(review):
                    suppressed_by = f.name
                    break
            except Exception:
                pass
        if suppressed_by:
            suppressed.append((review, suppressed_by))
        else:
            kept.append(review)

    return FilterResult(kept=kept, suppressed=suppressed)
