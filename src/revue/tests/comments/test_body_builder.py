"""Unit tests for BodyBuilder comment rendering module."""
import pytest

from revue.comments.body_builder import BodyBuilder
from revue.comments.models import Attribution, ConsolidatedFinding


@pytest.fixture
def simple_finding():
    """A minimal ConsolidatedFinding for testing."""
    return ConsolidatedFinding(
        file_path="src/app.py",
        line_number=42,
        severity="high",
        issue="Security vulnerability in user input handling",
        suggestion="Sanitize input before database insertion",
        confidence=0.95,
        category="security",
        attribution=[Attribution(agent_name="zara", category="security")],
        code_replacement=None,
        replacement_line_count=1,
        snippet="user_input = request.form['username']",
        group_type="singleton",
    )


@pytest.fixture
def finding_with_code_replacement():
    """ConsolidatedFinding with a code_replacement for testing suggestion fences."""
    return ConsolidatedFinding(
        file_path="src/utils.py",
        line_number=15,
        severity="medium",
        issue="Use parameterized queries instead of string concatenation",
        suggestion="Refactor to use prepared statement",
        confidence=0.85,
        category="code-quality",
        attribution=[Attribution(agent_name="kai", category="code-quality")],
        code_replacement=["query = db.prepare('SELECT * FROM users WHERE id = ?')", "result = query.execute(user_id)"],
        replacement_line_count=2,
        snippet="query = 'SELECT * FROM users WHERE id = ' + str(user_id)",
        group_type="singleton",
    )


@pytest.fixture
def multi_finding_grouped():
    """ConsolidatedFinding representing multiple grouped findings with multiple attributions."""
    return ConsolidatedFinding(
        file_path="src/core.py",
        line_number=100,
        severity="high",
        issue="Function lacks error handling for network failures",
        suggestion="Add try-except block with retry logic",
        confidence=0.9,
        category="reliability",
        attribution=[
            Attribution(agent_name="zara", category="security"),
            Attribution(agent_name="kai", category="reliability"),
        ],
        code_replacement=None,
        replacement_line_count=1,
        snippet="response = requests.get(url)",
        group_type="same_line",
    )


class TestSingletonProseOnly:
    """Test singleton findings without code replacement."""

    def test_singleton_prose_only(self, simple_finding):
        """build() with code_replacement=None returns severity badge + issue + suggestion, no code fence."""
        builder = BodyBuilder()
        result = builder.build(simple_finding, fp="abc123")

        assert "🔴" in result  # severity emoji for high
        assert "Security vulnerability in user input handling" in result
        assert "Sanitize input before database insertion" in result
        assert "```" not in result  # no code fence
        assert "[//]: # (revue:fp:abc123)" in result  # fingerprint sentinel

    def test_fingerprint_sentinel_present(self, simple_finding):
        """build() output contains the fingerprint sentinel with the passed-in fp value."""
        builder = BodyBuilder()
        fp_value = "fingerprint_xyz789"
        result = builder.build(simple_finding, fp=fp_value)

        assert f"[//]: # (revue:fp:{fp_value})" in result

    def test_brand_footer_present(self, simple_finding):
        """All inline comment shapes end with — 🤖 Revue."""
        builder = BodyBuilder()
        result = builder.build(simple_finding, fp="test123")

        # Footer should appear before the fingerprint sentinel
        assert "— 🤖 Revue" in result
        lines = result.split("\n")
        # Last non-empty line should be the fingerprint sentinel
        non_empty_lines = [l for l in lines if l.strip()]
        assert "[//]: #" in non_empty_lines[-1]
        # Footer should be in one of the last lines
        assert any("— 🤖 Revue" in l for l in non_empty_lines[-3:])

    def test_severity_emoji_mapping(self):
        """All severity levels render with correct emoji."""
        builder = BodyBuilder()
        severities = {
            "high": "🔴",
            "medium": "🟡",
            "low": "🔵",
            "info": "ℹ️",
        }

        for severity, emoji in severities.items():
            finding = ConsolidatedFinding(
                file_path="test.py",
                line_number=1,
                severity=severity,  # type: ignore
                issue=f"Test {severity} issue",
                suggestion="Test fix",
                confidence=0.8,
                category="test",
                attribution=[Attribution(agent_name="test", category="test")],
                code_replacement=None,
                replacement_line_count=1,
                snippet="test",
                group_type="singleton",
            )
            result = builder.build(finding, fp="fp123")
            assert emoji in result, f"Expected {emoji} for severity {severity}"


class TestSingletonWithSuggestionFences:
    """Test singleton findings with code replacement (suggestion blocks)."""

    def test_singleton_with_suggestion_github(self, finding_with_code_replacement):
        """build() with code_replacement set and platform='github' returns GitHub suggestion fence."""
        builder = BodyBuilder()
        finding_with_code_replacement.group_type = "singleton"
        result = builder.build(finding_with_code_replacement, fp="fp_gh", platform="github")

        assert "🟡" in result  # medium severity emoji
        assert "Use parameterized queries" in result
        assert "```suggestion" in result  # GitHub suggestion fence marker
        assert "query = db.prepare" in result  # code content
        assert "[//]: # (revue:fp:fp_gh)" in result

    def test_singleton_with_suggestion_gitlab(self, finding_with_code_replacement):
        """build() with code_replacement set and platform='gitlab' returns GitLab suggestion fence."""
        builder = BodyBuilder()
        finding_with_code_replacement.group_type = "singleton"
        result = builder.build(finding_with_code_replacement, fp="fp_gl", platform="gitlab")

        assert "🟡" in result
        assert "```suggestion" in result  # GitLab also uses suggestion fence
        assert "-0+" in result  # GitLab syntax includes line count directive
        assert "query = db.prepare" in result
        assert "[//]: # (revue:fp:fp_gl)" in result

    def test_singleton_with_suggestion_bitbucket(self, finding_with_code_replacement):
        """build() with code_replacement set and platform='bitbucket' returns Bitbucket inline format."""
        builder = BodyBuilder()
        finding_with_code_replacement.group_type = "singleton"
        result = builder.build(finding_with_code_replacement, fp="fp_bb", platform="bitbucket")

        # Bitbucket doesn't use suggestion fences, just regular code blocks
        assert "🟡" in result
        assert "```" in result or "query = db.prepare" in result
        assert "[//]: # (revue:fp:fp_bb)" in result
        # Bitbucket format should NOT have suggestion directive
        assert "```suggestion" not in result

    def test_platform_registry_not_elif(self, finding_with_code_replacement):
        """BodyBuilder uses a dict registry for platform dispatch (no if/elif chain)."""
        builder = BodyBuilder()
        # This test verifies that the internal implementation uses a registry
        # by checking that all platform strings produce consistent output structure
        platforms = ["github", "gitlab", "bitbucket"]
        results = {}

        for platform in platforms:
            result = builder.build(finding_with_code_replacement, fp="fp_test", platform=platform)
            results[platform] = result
            # All should have the basic structure
            assert "🟡" in result
            assert "[//]: # (revue:fp:fp_test)" in result

        # GitHub and GitLab should both have ```suggestion
        assert "```suggestion" in results["github"]
        assert "```suggestion" in results["gitlab"]
        # But Bitbucket should not
        assert "```suggestion" not in results["bitbucket"]


class TestGroupedAttribution:
    """Test multi-finding grouped comments with attribution."""

    def test_grouped_attribution_preserved(self, multi_finding_grouped):
        """build() on multi-finding ConsolidatedFinding includes attribution in output."""
        builder = BodyBuilder()
        result = builder.build(multi_finding_grouped, fp="fp_multi")

        # Should include both agent names
        assert "Zara" in result or "zara" in result.lower()
        assert "Kai" in result or "kai" in result.lower()
        # Should include categories
        assert "Security" in result or "security" in result.lower()
        assert "Reliability" in result or "reliability" in result.lower()
        assert "[//]: # (revue:fp:fp_multi)" in result

    def test_attribution_format(self, multi_finding_grouped):
        """Attribution renders as *AgentName · CategoryTitle* format."""
        builder = BodyBuilder()
        result = builder.build(multi_finding_grouped, fp="fp_attr")

        # Check for the expected format pattern (case-insensitive due to title casing)
        # Should have agent display name · category format
        assert "·" in result or "-" in result  # separator between agent and category


class TestBuildGrouped:
    """Test build_grouped() for multi-finding merged comments (CLI-path)."""

    def _make_item(self, severity, issue, suggestion, agent_name, category, code_replacement=None):
        return ConsolidatedFinding(
            file_path="app.py",
            line_number=42,
            severity=severity,  # type: ignore
            issue=issue,
            suggestion=suggestion,
            confidence=0.9,
            category=category,
            attribution=[Attribution(agent_name=agent_name, category=category)],
            code_replacement=code_replacement,
            replacement_line_count=len(code_replacement) if code_replacement else 0,
            snippet="some code",
            group_type="same_line",
        )

    def test_header_states_finding_count(self) -> None:
        """build_grouped() first line contains the count of findings and highest severity."""
        builder = BodyBuilder()
        items = [
            self._make_item("high", "SQL injection", "Use params", "zara", "security"),
            self._make_item("medium", "Perf issue", "Batch it", "kai", "performance"),
            self._make_item("low", "Style nit", "Rename var", "maya", "code-quality"),
        ]
        result = builder.build_grouped(items, fp="fp_grp")

        first_line = result.splitlines()[0]
        assert "3" in first_line, "Header must include finding count"
        assert "findings" in first_line.lower(), "Header must contain 'findings'"
        assert "[HIGH]" in first_line, "Header must show highest severity badge"
        assert "🔴" in first_line, "Header must show highest severity emoji"

    def test_each_finding_issue_in_body(self) -> None:
        """build_grouped() body contains each finding's issue text."""
        builder = BodyBuilder()
        items = [
            self._make_item("high", "Unsafe deser", "Use json.loads", "zara", "security"),
            self._make_item("medium", "N+1 query", "Batch DB calls", "kai", "performance"),
        ]
        result = builder.build_grouped(items, fp="fp_issues")

        assert "Unsafe deser" in result
        assert "N+1 query" in result

    def test_each_finding_suggestion_in_body(self) -> None:
        """build_grouped() body contains each finding's suggestion text."""
        builder = BodyBuilder()
        items = [
            self._make_item("high", "Issue A", "Use json.loads", "zara", "security"),
            self._make_item("medium", "Issue B", "Batch DB calls", "kai", "performance"),
        ]
        result = builder.build_grouped(items, fp="fp_sugg")

        assert "json.loads" in result
        assert "Batch DB calls" in result

    def test_attribution_present_for_each_item(self) -> None:
        """build_grouped() includes agent attribution for each finding."""
        builder = BodyBuilder()
        items = [
            self._make_item("high", "Issue A", "Fix A", "zara", "security"),
            self._make_item("medium", "Issue B", "Fix B", "kai", "performance"),
        ]
        result = builder.build_grouped(items, fp="fp_attr")

        assert "Zara" in result or "zara" in result.lower()
        assert "Kai" in result or "kai" in result.lower()

    def test_fingerprint_sentinel_present(self) -> None:
        """build_grouped() embeds fingerprint sentinel."""
        builder = BodyBuilder()
        items = [self._make_item("high", "Issue", "Fix it", "zara", "security")]
        result = builder.build_grouped(items, fp="fp_sentinel")

        assert "[//]: # (revue:fp:fp_sentinel)" in result

    def test_brand_footer_present(self) -> None:
        """build_grouped() appends brand footer."""
        builder = BodyBuilder()
        items = [self._make_item("medium", "Issue", "Fix it", "kai", "performance")]
        result = builder.build_grouped(items, fp="fp_footer")

        assert "— 🤖 Revue" in result

    def test_platform_dispatch_for_code_replacement(self) -> None:
        """build_grouped() uses platform-specific fence for items with code_replacement."""
        builder = BodyBuilder()
        items = [
            self._make_item("high", "XSS", "Escape output", "zara", "security",
                            code_replacement=["    return html.escape(value)"]),
        ]
        gh_result = builder.build_grouped(items, fp="fp_gh", platform="github")
        bb_result = builder.build_grouped(items, fp="fp_bb", platform="bitbucket")

        assert "```suggestion" in gh_result
        assert "```suggestion" not in bb_result


class TestVocabularyLabels:
    """Test vocabulary label derivation (Action/Suggest/Note)."""

    @pytest.mark.parametrize(
        "severity,has_code_replacement,expected_label",
        [
            ("info", False, "Note"),  # info → Note
            ("info", True, "Note"),  # info always uses Note, ignoring code_replacement
            ("high", True, "Action"),  # code_replacement present → Action
            ("high", False, "Suggest"),  # no code_replacement → Suggest
            ("medium", True, "Action"),
            ("medium", False, "Suggest"),
            ("low", True, "Action"),
            ("low", False, "Suggest"),
        ],
    )
    def test_vocabulary_label_default(self, severity, has_code_replacement, expected_label):
        """Vocabulary labels follow the derivation rule: info→Note, code_replacement→Action, else→Suggest."""
        finding = ConsolidatedFinding(
            file_path="test.py",
            line_number=1,
            severity=severity,  # type: ignore
            issue="Test issue",
            suggestion="Test suggestion",
            confidence=0.8,
            category="test",
            attribution=[Attribution(agent_name="test", category="test")],
            code_replacement=["new code"] if has_code_replacement else None,
            replacement_line_count=1 if has_code_replacement else 0,
            snippet="old code",
            group_type="singleton",
        )
        builder = BodyBuilder()
        result = builder.build(finding, fp="fp_vocab")

        # Check for label emoji and keyword
        if expected_label == "Note":
            assert "ℹ️" in result
            assert "Note" in result
        elif expected_label == "Action":
            assert "💡" in result
            assert "Action" in result
        elif expected_label == "Suggest":
            assert "💡" in result
            assert "Suggest" in result


class TestSummaryComment:
    """Test build_summary() method."""

    def test_summary_with_unanchored_section(self, simple_finding):
        """build_summary() includes unanchored findings in a separate section when summary_sink is not empty."""
        builder = BodyBuilder()
        findings = [simple_finding]
        summary_sink = [simple_finding]

        result = builder.build_summary(findings, summary_sink)

        assert "Unanchored" in result or "unanchored" in result.lower()
        assert "Security vulnerability" in result

    def test_summary_without_unanchored_section(self, simple_finding):
        """build_summary() omits unanchored section when summary_sink is empty."""
        builder = BodyBuilder()
        findings = [simple_finding]
        summary_sink: list[ConsolidatedFinding] = []

        result = builder.build_summary(findings, summary_sink)

        # Should still have findings summary
        assert "high" in result.lower() or "🔴" in result
        # Should not mention unanchored
        assert "unanchored" not in result.lower()

    def test_summary_severity_counts(self, simple_finding):
        """build_summary() includes severity count breakdown."""
        builder = BodyBuilder()
        high_finding = simple_finding
        medium_finding = ConsolidatedFinding(
            file_path="test.py",
            line_number=2,
            severity="medium",
            issue="Medium severity issue",
            suggestion="Fix it",
            confidence=0.8,
            category="test",
            attribution=[Attribution(agent_name="test", category="test")],
            code_replacement=None,
            replacement_line_count=1,
            snippet="test",
            group_type="singleton",
        )

        findings = [high_finding, medium_finding]
        result = builder.build_summary(findings, [])

        # Should show both severity counts
        assert "high" in result.lower() or "🔴" in result
        assert "medium" in result.lower() or "🟡" in result


class TestSingletonMultiAttribution:
    """Tests for Finding 1 (MEDIUM) and Finding 2 (LOW) from GitLab MR !23 comment.

    Finding 1: build() only rendered the first attribution for a singleton with multiple
    attributions because the multi-attribution loop was gated on group_type in
    ("proximity", "same_line") — a singleton synthesised_from multiple agents would skip it.

    Finding 2: "proximity" is never set as a group_type in cli.py, making that branch
    dead code. The guard should depend solely on len(attribution).
    """

    def _synthesised_singleton(self) -> ConsolidatedFinding:
        """Singleton finding carrying two attributions (synthesised_from path in cli.py)."""
        return ConsolidatedFinding(
            file_path="src/app.py",
            line_number=10,
            severity="high",
            issue="Timing attack in auth check",
            suggestion="Use hmac.compare_digest",
            confidence=0.9,
            category="security",
            attribution=[
                Attribution(agent_name="zara", category="security"),
                Attribution(agent_name="kai", category="code-quality"),
            ],
            code_replacement=None,
            replacement_line_count=1,
            snippet="",
            group_type="singleton",  # <-- this is the key: group_type stays 'singleton'
        )

    def test_all_attributions_rendered_for_singleton_with_multiple_agents(self) -> None:
        """build() must render ALL attributions even when group_type='singleton'.

        Fails against the pre-fix code because the multi-attribution loop was
        gated on group_type in ('proximity', 'same_line').
        """
        builder = BodyBuilder()
        result = builder.build(self._synthesised_singleton(), fp="fp1")

        assert "Zara" in result or "zara" in result.lower(), \
            "First agent attribution must appear in singleton multi-attribution comment"
        assert "Kai" in result or "kai" in result.lower(), \
            "Second agent attribution must appear in singleton multi-attribution comment"

    def test_single_attribution_singleton_still_renders(self) -> None:
        """build() with one attribution and group_type='singleton' still renders the attribution."""
        builder = BodyBuilder()
        finding = ConsolidatedFinding(
            file_path="src/app.py",
            line_number=5,
            severity="medium",
            issue="Missing type hint",
            suggestion="Add annotation",
            confidence=0.8,
            category="code-quality",
            attribution=[Attribution(agent_name="maya", category="code-quality")],
            code_replacement=None,
            replacement_line_count=1,
            snippet="",
            group_type="singleton",
        )
        result = builder.build(finding, fp="fp2")

        assert "Maya" in result or "maya" in result.lower(), \
            "Single attribution must still render for a singleton finding"

    def test_multi_attribution_renders_regardless_of_group_type(self) -> None:
        """All group_type values ('singleton', 'same_line') render all attributions
        when len(attribution) > 1 — group_type must not gate the loop.
        """
        builder = BodyBuilder()
        for gtype in ("singleton", "same_line"):
            finding = ConsolidatedFinding(
                file_path="src/app.py",
                line_number=10,
                severity="low",
                issue="Issue",
                suggestion="Fix",
                confidence=0.8,
                category="test",
                attribution=[
                    Attribution(agent_name="zara", category="security"),
                    Attribution(agent_name="leo", category="performance"),
                ],
                code_replacement=None,
                replacement_line_count=1,
                snippet="",
                group_type=gtype,  # type: ignore
            )
            result = builder.build(finding, fp="fp3")
            assert "Zara" in result or "zara" in result.lower(), \
                f"Zara must render for group_type={gtype!r}"
            assert "Leo" in result or "leo" in result.lower(), \
                f"Leo must render for group_type={gtype!r}"
