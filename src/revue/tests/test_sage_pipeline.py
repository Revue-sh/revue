#!/usr/bin/env python3
"""
Tests for Sage pipeline orchestration.
"""

import pytest
from unittest.mock import Mock, MagicMock
from revue.core.sage_pipeline import run_sage_analysis, SageSummary
from revue.core.models import AIReview, CodeFix, FixabilityResult
from revue.core.vcs_adapter import DiffPosition


class TestSageSummary:
    """Test SageSummary data model and markdown generation."""
    
    def test_summary_to_markdown_all_categories(self):
        """Summary with all three categories should render all sections."""
        summary = SageSummary(
            auto_fixable_count=3,
            manual_review_count=5,
            unfixable_count=2,
            posted_suggestions_count=3,
            total_findings=10
        )
        
        markdown = summary.to_markdown()
        
        assert "🔧 **Auto-fixable:** 3 issue(s)" in markdown
        assert "3 suggestion(s) posted" in markdown
        assert "⚠️ **Needs manual review:** 5 issue(s)" in markdown
        assert "❌ **Unfixable:** 2 issue(s)" in markdown
    
    def test_summary_to_markdown_no_findings(self):
        """Summary with zero findings should show appropriate message."""
        summary = SageSummary(
            auto_fixable_count=0,
            manual_review_count=0,
            unfixable_count=0,
            posted_suggestions_count=0,
            total_findings=0
        )
        
        markdown = summary.to_markdown()
        
        assert "No findings to review" in markdown
    
    def test_summary_to_markdown_only_fixable(self):
        """Summary with only fixable findings."""
        summary = SageSummary(
            auto_fixable_count=5,
            manual_review_count=0,
            unfixable_count=0,
            posted_suggestions_count=5,
            total_findings=5
        )
        
        markdown = summary.to_markdown()
        
        assert "🔧 **Auto-fixable:** 5 issue(s)" in markdown
        assert "⚠️" not in markdown
        assert "❌" not in markdown


class TestRunSageAnalysis:
    """Test full Sage pipeline orchestration."""
    
    def test_run_sage_all_fixable(self, monkeypatch):
        """All findings fixable → all should be posted."""
        # Mock classifier
        def mock_classify(finding, diff, file_content):
            return FixabilityResult(
                is_fixable=True,
                confidence=85.0,
                category="self-contained",
                reason="Test fixable"
            )
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.classify_finding", mock_classify
        )
        
        # Mock generator
        def mock_generate(finding, file_content, diff, ai_client):
            return CodeFix(
                original_lines=["old"],
                fixed_lines=["new"],
                start_line=finding.line_number,
                end_line=finding.line_number,
                confidence=80.0,
                explanation="Fixed it"
            )
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.generate_fix", mock_generate
        )
        
        # Mock VCS adapter
        mock_adapter = Mock()
        mock_adapter.resolve_position = Mock(
            return_value=DiffPosition(
                file_path="test.py",
                line_number=10,
                position=5
            )
        )
        mock_adapter.post_suggested_change = Mock(return_value=True)
        
        # Mock AIClient
        mock_ai_client = Mock()
        
        findings = [
            AIReview(
                file_path="test.py",
                line_number=10,
                severity="critical",
                issue="Hardcoded secret",
                suggestion="Use env var",
                confidence=0.90,
                category="security-analyst"
            ),
            AIReview(
                file_path="test.py",
                line_number=20,
                severity="major",
                issue="SQL injection",
                suggestion="Use parameterized query",
                confidence=0.85,
                category="security-analyst"
            ),
        ]
        
        result = run_sage_analysis(
            findings=findings,
            diff="sample diff",
            file_contents={"test.py": "file content"},
            ai_client=mock_ai_client,
            vcs_adapter=mock_adapter,
            pr_id=42,
            platform="github"
        )
        
        assert result.auto_fixable_count == 2
        assert result.manual_review_count == 0
        assert result.unfixable_count == 0
        assert result.posted_suggestions_count == 2
        assert result.total_findings == 2
        
        # Verify VCS adapter was called twice
        assert mock_adapter.post_suggested_change.call_count == 2
    
    def test_run_sage_mixed_categories(self, monkeypatch):
        """Mix of fixable, context-dependent, and unfixable findings."""
        classification_sequence = [
            FixabilityResult(True, 85.0, "self-contained", "Fixable"),
            FixabilityResult(False, 90.0, "context-dependent", "Needs context"),
            FixabilityResult(False, 100.0, "unfixable", "Not in diff"),
        ]
        
        call_count = {"index": 0}
        
        def mock_classify(finding, diff, file_content):
            result = classification_sequence[call_count["index"]]
            call_count["index"] += 1
            return result
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.classify_finding", mock_classify
        )
        
        def mock_generate(finding, file_content, diff, ai_client):
            return CodeFix(
                original_lines=["old"],
                fixed_lines=["new"],
                start_line=10,
                end_line=10,
                confidence=80.0,
                explanation="Fix"
            )
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.generate_fix", mock_generate
        )
        
        mock_adapter = Mock()
        mock_adapter.resolve_position = Mock(
            return_value=DiffPosition("test.py", 10, 5)
        )
        mock_adapter.post_suggested_change = Mock(return_value=True)
        
        findings = [
            AIReview("test.py", 10, "critical", "Issue 1", "Fix 1", 0.9, "security"),
            AIReview("test.py", 20, "major", "Issue 2", "Fix 2", 0.8, "architecture"),
            AIReview("test.py", 30, "minor", "Issue 3", "Fix 3", 0.7, "quality"),
        ]
        
        result = run_sage_analysis(
            findings=findings,
            diff="diff",
            file_contents={"test.py": "content"},
            ai_client=Mock(),
            vcs_adapter=mock_adapter,
            pr_id=42,
            platform="github"
        )
        
        assert result.auto_fixable_count == 1
        assert result.manual_review_count == 1
        assert result.unfixable_count == 1
        assert result.posted_suggestions_count == 1
    
    def test_run_sage_generator_fails(self, monkeypatch):
        """Generator returning None should count as manual review."""
        def mock_classify(finding, diff, file_content):
            return FixabilityResult(True, 85.0, "self-contained", "Fixable")
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.classify_finding", mock_classify
        )
        
        # Generator returns None (AI declined)
        def mock_generate(finding, file_content, diff, ai_client):
            return None
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.generate_fix", mock_generate
        )
        
        mock_adapter = Mock()
        
        findings = [
            AIReview("test.py", 10, "major", "Issue", "Fix", 0.8, "quality"),
        ]
        
        result = run_sage_analysis(
            findings=findings,
            diff="diff",
            file_contents={"test.py": "content"},
            ai_client=Mock(),
            vcs_adapter=mock_adapter,
            pr_id=42,
            platform="github"
        )
        
        # Should be counted as manual review, not auto-fixable
        assert result.auto_fixable_count == 0
        assert result.manual_review_count == 1
        assert result.posted_suggestions_count == 0
    
    def test_run_sage_gitlab_platform(self, monkeypatch):
        """GitLab platform should call post_apply_suggestion."""
        def mock_classify(finding, diff, file_content):
            return FixabilityResult(True, 90.0, "self-contained", "Fixable")
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.classify_finding", mock_classify
        )
        
        def mock_generate(finding, file_content, diff, ai_client):
            return CodeFix(["old"], ["new"], 10, 10, 85.0, "Fix")
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.generate_fix", mock_generate
        )
        
        mock_adapter = Mock()
        mock_adapter.resolve_position = Mock(
            return_value=DiffPosition("test.py", 10, line_code="abc123")
        )
        mock_adapter.post_apply_suggestion = Mock(return_value=True)
        
        findings = [
            AIReview("test.py", 10, "critical", "Issue", "Fix", 0.9, "security"),
        ]
        
        result = run_sage_analysis(
            findings=findings,
            diff="diff",
            file_contents={"test.py": "content"},
            ai_client=Mock(),
            vcs_adapter=mock_adapter,
            pr_id=5,
            platform="gitlab"
        )
        
        # Should call GitLab method
        assert mock_adapter.post_apply_suggestion.call_count == 1
        assert result.posted_suggestions_count == 1
    
    def test_run_sage_posting_fails_gracefully(self, monkeypatch):
        """VCS posting failure should not crash pipeline."""
        def mock_classify(finding, diff, file_content):
            return FixabilityResult(True, 85.0, "self-contained", "Fixable")
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.classify_finding", mock_classify
        )
        
        def mock_generate(finding, file_content, diff, ai_client):
            return CodeFix(["old"], ["new"], 10, 10, 80.0, "Fix")
        
        monkeypatch.setattr(
            "revue.core.sage_pipeline.generate_fix", mock_generate
        )
        
        mock_adapter = Mock()
        mock_adapter.resolve_position = Mock(
            return_value=DiffPosition("test.py", 10, 5)
        )
        # Posting fails
        mock_adapter.post_suggested_change = Mock(return_value=False)
        
        findings = [
            AIReview("test.py", 10, "critical", "Issue", "Fix", 0.9, "security"),
        ]
        
        result = run_sage_analysis(
            findings=findings,
            diff="diff",
            file_contents={"test.py": "content"},
            ai_client=Mock(),
            vcs_adapter=mock_adapter,
            pr_id=42,
            platform="github"
        )
        
        # Should still count as fixable, but posted count = 0
        assert result.auto_fixable_count == 1
        assert result.posted_suggestions_count == 0
