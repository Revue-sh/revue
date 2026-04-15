"""Tests for noise filters."""
from __future__ import annotations

import pytest

from revue.core.noise_filters import (
    apply_noise_filters, build_filters,
    LowConfidenceFilter, TestFileFilter,
    GeneratedFileFilter, EmptyIssueFilter, FilterResult,
    SwiftDIFilter, KotlinDIFilter, JavaDIFilter, TypeScriptDIFilter,
    LinterSuppressionFilter,
)
from revue.core.models import AIReview


def _review(file_path="app.py", severity="minor",
            issue="test issue", confidence=0.8) -> AIReview:
    return AIReview(
        file_path=file_path, line_number=10, severity=severity,
        issue=issue, suggestion="fix it", confidence=confidence,
    )


def test_no_filters_keeps_all():
    findings = [_review(), _review(file_path="b.py")]
    result = apply_noise_filters(findings, filters=[])
    assert result.kept_count == 2
    assert result.suppressed_count == 0


def test_low_confidence_suppressed():
    low = _review(confidence=0.3)
    high = _review(confidence=0.9, file_path="b.py")
    result = apply_noise_filters([low, high], filters=[LowConfidenceFilter(threshold=0.5)])
    assert result.kept_count == 1
    assert result.suppressed_count == 1
    assert result.suppressed[0][1] == "low-confidence"


def test_test_file_suppresses_non_critical():
    test_finding = _review(file_path="tests/test_app.py", severity="minor")
    result = apply_noise_filters([test_finding], filters=[TestFileFilter()])
    assert result.suppressed_count == 1


def test_test_file_keeps_critical():
    high = _review(file_path="tests/test_app.py", severity="high")
    result = apply_noise_filters([high], filters=[TestFileFilter()])
    assert result.kept_count == 1


def test_test_file_keeps_high_severity():
    """B2 regression: TestFileFilter must guard on 'high' (normalised), not 'critical'."""
    high = _review(file_path="tests/test_app.py", severity="high")
    result = apply_noise_filters([high], filters=[TestFileFilter()])
    assert result.kept_count == 1, (
        "High-severity findings in test files must NOT be suppressed"
    )


def test_generated_file_suppressed():
    gen = _review(file_path="src/api.pb.go")
    result = apply_noise_filters([gen], filters=[GeneratedFileFilter()])
    assert result.suppressed_count == 1


def test_empty_issue_suppressed():
    empty = _review(issue="   ")
    result = apply_noise_filters([empty], filters=[EmptyIssueFilter()])
    assert result.suppressed_count == 1


def test_filter_failure_is_non_fatal():
    class _BrokenFilter:
        name = "broken"
        def should_suppress(self, review):
            raise RuntimeError("filter broke")

    findings = [_review()]
    result = apply_noise_filters(findings, filters=[_BrokenFilter()])
    assert isinstance(result, FilterResult)
    assert result.kept_count == 1  # not suppressed because filter failed


def test_first_matching_filter_wins():
    """Short-circuit: only first matching filter name recorded."""
    low = _review(confidence=0.3, issue="")
    result = apply_noise_filters(
        [low],
        filters=[LowConfidenceFilter(threshold=0.5), EmptyIssueFilter()]
    )
    assert result.suppressed[0][1] == "low-confidence"


def test_multiple_findings_partial_suppression():
    findings = [
        _review(confidence=0.9),
        _review(confidence=0.2, file_path="b.py"),
        _review(confidence=0.8, file_path="c.py"),
    ]
    result = apply_noise_filters(findings, filters=[LowConfidenceFilter(0.5)])
    assert result.kept_count == 2
    assert result.suppressed_count == 1


# ---------------------------------------------------------------------------
# Swift DI filter (Swinject)
# ---------------------------------------------------------------------------

class TestSwiftDIFilter:
    def test_suppresses_swinject_register_noise(self) -> None:
        finding = _review(
            file_path="Sources/DI/AppAssembly.swift",
            issue="container.register(Service.self) — unused return value",
            confidence=0.8,
        )
        result = apply_noise_filters([finding], filters=[SwiftDIFilter()])
        assert result.suppressed_count == 1
        assert result.suppressed[0][1] == "swift-di"

    def test_does_not_suppress_non_swift_file(self) -> None:
        finding = _review(
            file_path="src/di/container.py",
            issue="container.register call — unused return value",
            confidence=0.8,
        )
        result = apply_noise_filters([finding], filters=[SwiftDIFilter()])
        assert result.kept_count == 1

    def test_does_not_suppress_unrelated_swift_finding(self) -> None:
        finding = _review(
            file_path="Sources/Auth/LoginView.swift",
            issue="Force unwrap on optional value",
            confidence=0.9,
        )
        result = apply_noise_filters([finding], filters=[SwiftDIFilter()])
        assert result.kept_count == 1


# ---------------------------------------------------------------------------
# Kotlin DI filter (Koin)
# ---------------------------------------------------------------------------

class TestKotlinDIFilter:
    def test_suppresses_koin_module_noise(self) -> None:
        finding = _review(
            file_path="app/src/main/AppModule.kt",
            issue="single { UserRepository() } — consider using factory instead",
            confidence=0.7,
        )
        result = apply_noise_filters([finding], filters=[KotlinDIFilter()])
        assert result.suppressed_count == 1
        assert result.suppressed[0][1] == "kotlin-di"

    def test_does_not_suppress_non_kotlin_file(self) -> None:
        finding = _review(
            file_path="src/AppModule.java",
            issue="single { UserRepository() } pattern",
            confidence=0.7,
        )
        result = apply_noise_filters([finding], filters=[KotlinDIFilter()])
        assert result.kept_count == 1


# ---------------------------------------------------------------------------
# Java DI filter (Dagger)
# ---------------------------------------------------------------------------

class TestJavaDIFilter:
    def test_suppresses_dagger_inject_noise(self) -> None:
        finding = _review(
            file_path="src/main/java/com/app/UserService.java",
            issue="@Inject constructor — field injection preferred",
            confidence=0.75,
        )
        result = apply_noise_filters([finding], filters=[JavaDIFilter()])
        assert result.suppressed_count == 1
        assert result.suppressed[0][1] == "java-di"

    def test_does_not_suppress_non_java_file(self) -> None:
        finding = _review(
            file_path="src/UserService.kt",
            issue="@Inject constructor pattern",
            confidence=0.75,
        )
        result = apply_noise_filters([finding], filters=[JavaDIFilter()])
        assert result.kept_count == 1


# ---------------------------------------------------------------------------
# TypeScript DI filter (Angular / NestJS)
# ---------------------------------------------------------------------------

class TestTypeScriptDIFilter:
    def test_suppresses_angular_injectable_noise(self) -> None:
        finding = _review(
            file_path="src/app/services/user.service.ts",
            issue="@Injectable() decorator — providedIn may be redundant",
            confidence=0.7,
        )
        result = apply_noise_filters([finding], filters=[TypeScriptDIFilter()])
        assert result.suppressed_count == 1
        assert result.suppressed[0][1] == "typescript-di"

    def test_does_not_suppress_non_typescript_file(self) -> None:
        finding = _review(
            file_path="src/service.py",
            issue="@Injectable pattern",
            confidence=0.7,
        )
        result = apply_noise_filters([finding], filters=[TypeScriptDIFilter()])
        assert result.kept_count == 1


# ---------------------------------------------------------------------------
# Linter suppression filter
# ---------------------------------------------------------------------------

class TestLinterSuppressionFilter:
    def test_suppresses_swiftlint_disable_comment(self) -> None:
        finding = _review(
            file_path="Sources/Auth.swift",
            issue="swiftlint:disable force_cast — suppressed rule",
            confidence=0.8,
        )
        result = apply_noise_filters([finding], filters=[LinterSuppressionFilter()])
        assert result.suppressed_count == 1
        assert result.suppressed[0][1] == "linter-suppression"

    def test_suppresses_eslint_disable(self) -> None:
        finding = _review(
            file_path="src/app.ts",
            issue="eslint-disable-next-line no-console — suppressed",
            confidence=0.8,
        )
        result = apply_noise_filters([finding], filters=[LinterSuppressionFilter()])
        assert result.suppressed_count == 1

    def test_suppresses_detekt_suppress(self) -> None:
        finding = _review(
            file_path="app/MainActivity.kt",
            issue="@Suppress(\"MagicNumber\") annotation present",
            confidence=0.7,
        )
        result = apply_noise_filters([finding], filters=[LinterSuppressionFilter()])
        assert result.suppressed_count == 1

    def test_suppresses_python_noqa(self) -> None:
        finding = _review(
            file_path="app/utils.py",
            issue="Line flagged with # noqa: E501",
            confidence=0.8,
        )
        result = apply_noise_filters([finding], filters=[LinterSuppressionFilter()])
        assert result.suppressed_count == 1

    def test_non_suppressed_finding_passes(self) -> None:
        finding = _review(
            file_path="src/auth.py",
            issue="SQL injection via string concatenation",
            confidence=0.9,
        )
        result = apply_noise_filters([finding], filters=[LinterSuppressionFilter()])
        assert result.kept_count == 1


# ---------------------------------------------------------------------------
# build_filters — configurable filter set
# ---------------------------------------------------------------------------

class TestBuildFilters:
    def test_default_includes_all_filters(self) -> None:
        filters = build_filters()
        names = {f.name for f in filters}
        assert "low-confidence" in names
        assert "swift-di" in names
        assert "kotlin-di" in names
        assert "java-di" in names
        assert "typescript-di" in names
        assert "linter-suppression" in names

    def test_disable_removes_named_filter(self) -> None:
        filters = build_filters(disabled=["swift-di", "linter-suppression"])
        names = {f.name for f in filters}
        assert "swift-di" not in names
        assert "linter-suppression" not in names
        assert "kotlin-di" in names  # other filters still present

    def test_custom_low_confidence_threshold(self) -> None:
        filters = build_filters(low_confidence_threshold=0.8)
        lc = next(f for f in filters if f.name == "low-confidence")
        assert isinstance(lc, LowConfidenceFilter)
        assert lc.threshold == 0.8

    def test_disable_all_di_filters(self) -> None:
        filters = build_filters(disabled=["swift-di", "kotlin-di", "java-di", "typescript-di"])
        names = {f.name for f in filters}
        assert not any(n in names for n in ["swift-di", "kotlin-di", "java-di", "typescript-di"])
        assert "low-confidence" in names  # non-DI filters still present
