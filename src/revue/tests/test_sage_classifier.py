#!/usr/bin/env python3
"""
Tests for Sage fixability classifier.
"""

import pytest
from revue.core.sage_classifier import (
    classify_finding,
    _is_line_in_diff,
    _match_fixable_patterns,
    _apply_agent_rules,
    DEFAULT_MIN_CONFIDENCE,
)
from revue.core.models import AIReview, FixabilityResult


# Sample diff for testing
SAMPLE_DIFF = """diff --git a/app/auth.py b/app/auth.py
index 1234567..89abcdef 100644
--- a/app/auth.py
+++ b/app/auth.py
@@ -10,7 +10,8 @@ def login(username, password):
     # Authentication logic
-    api_key = "sk-1234567890abcdef"
+    # TODO: Move to environment variable
+    api_key = os.getenv("API_KEY")
     
     if not username:
         return None
@@ -25,6 +26,7 @@ def get_user(user_id):
     query = f"SELECT * FROM users WHERE id = {user_id}"
+    # Warning: SQL injection risk
     return db.execute(query)
"""


class TestIsLineInDiff:
    """Test diff line checking."""
    
    def test_line_in_added_section(self):
        """Line in + section should return True."""
        # Line 12 is the new api_key line
        assert _is_line_in_diff("app/auth.py", 12, SAMPLE_DIFF) is True
    
    def test_line_not_in_diff(self):
        """Line outside changed sections should return False."""
        assert _is_line_in_diff("app/auth.py", 50, SAMPLE_DIFF) is False
    
    def test_wrong_file(self):
        """Line in different file should return False."""
        assert _is_line_in_diff("app/other.py", 12, SAMPLE_DIFF) is False
    
    def test_empty_diff(self):
        """Empty diff should return False."""
        assert _is_line_in_diff("app/auth.py", 12, "") is False
    
    def test_malformed_diff(self):
        """Malformed diff should not crash, return False."""
        malformed = "not a real diff\nrandom text\n"
        assert _is_line_in_diff("app/auth.py", 12, malformed) is False


class TestMatchFixablePatterns:
    """Test pattern matching logic."""
    
    def test_hardcoded_secret_pattern(self):
        """Hardcoded API key should match."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=11,
            severity="critical",
            issue="Hardcoded API key detected",
            suggestion="Move to environment variable",
            confidence=0.95,
            category="security-analyst"
        )
        
        result = _match_fixable_patterns(finding)
        assert result is not None
        assert result["confidence"] == 90
        assert result["category"] == "self-contained"
    
    def test_sql_injection_pattern(self):
        """SQL injection with f-string should match."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=27,
            severity="critical",
            issue="SQL injection vulnerability",
            suggestion="Use parameterized query",
            confidence=0.90,
            category="security-analyst"
        )
        
        file_content = 'query = f"SELECT * FROM users WHERE id = {user_id}"'
        result = _match_fixable_patterns(finding, file_content)
        assert result is not None
        assert result["confidence"] == 85
    
    def test_no_pattern_match(self):
        """Generic finding should not match patterns."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=20,
            severity="minor",
            issue="Consider refactoring this function",
            suggestion="Split into smaller functions",
            confidence=0.70,
            category="code-quality-expert"
        )
        
        result = _match_fixable_patterns(finding)
        assert result is None


class TestApplyAgentRules:
    """Test agent-specific classification rules."""
    
    def test_architecture_agent_rule(self):
        """Architecture findings should be context-dependent."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=15,
            severity="major",
            issue="This violates single responsibility principle",
            suggestion="Extract authentication logic to separate service",
            confidence=0.85,
            category="architecture-reviewer"
        )
        
        result = _apply_agent_rules(finding)
        assert result is not None
        assert result["default_category"] == "context-dependent"
        assert result["default_confidence"] == 90
    
    def test_performance_agent_rule(self):
        """Performance findings should be context-dependent."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=27,
            severity="major",
            issue="N+1 query detected",
            suggestion="Add eager loading",
            confidence=0.80,
            category="performance-expert"
        )
        
        result = _apply_agent_rules(finding)
        assert result is not None
        assert result["default_category"] == "context-dependent"
    
    def test_no_agent_rule(self):
        """Generic agent should have no special rule."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=15,
            severity="minor",
            issue="Variable name could be clearer",
            suggestion="Rename to user_api_key",
            confidence=0.70,
            category="code-quality-expert"
        )
        
        result = _apply_agent_rules(finding)
        assert result is None


class TestClassifyFinding:
    """Test full classification logic."""
    
    def test_unfixable_line_not_in_diff(self):
        """Finding on unchanged line should be unfixable."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=50,  # Not in diff
            severity="minor",
            issue="Variable name unclear",
            suggestion="Rename variable",
            confidence=0.70,
            category="code-quality-expert"
        )
        
        result = classify_finding(finding, SAMPLE_DIFF)
        
        assert result.is_fixable is False
        assert result.confidence == 100.0
        assert result.category == "unfixable"
        assert "not in diff" in result.reason
    
    def test_self_contained_hardcoded_secret(self):
        """Hardcoded secret should be self-contained with high confidence."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=11,
            severity="critical",
            issue="Hardcoded API key detected",
            suggestion="Move to environment variable",
            confidence=0.95,
            category="security-analyst"
        )
        
        result = classify_finding(finding, SAMPLE_DIFF)
        
        assert result.is_fixable is True
        assert result.confidence >= 70
        assert result.category == "self-contained"
    
    def test_context_dependent_architecture(self):
        """Architecture finding should be context-dependent."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=12,  # In diff
            severity="major",
            issue="This function has too many responsibilities",
            suggestion="Split into separate functions",
            confidence=0.80,
            category="architecture-reviewer"
        )
        
        result = classify_finding(finding, SAMPLE_DIFF)
        
        assert result.is_fixable is False
        assert result.category == "context-dependent"
        assert "broader context" in result.reason
    
    def test_context_dependent_no_pattern(self):
        """Generic finding with no pattern should be context-dependent."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=12,
            severity="minor",
            issue="Consider adding type hints",
            suggestion="Add type annotations",
            confidence=0.65,
            category="code-quality-expert"
        )
        
        result = classify_finding(finding, SAMPLE_DIFF)
        
        assert result.is_fixable is False
        assert result.confidence == 50.0
        assert result.category == "context-dependent"
    
    def test_confidence_threshold_default(self):
        """Default threshold (70) — missing_null_check at confidence 70 is fixable."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=12,
            severity="minor",
            issue="Missing null check",
            suggestion="Add if not username check",
            confidence=0.60,
            category="code-quality-expert"
        )
        # missing_null_check pattern has confidence 70, equals DEFAULT_MIN_CONFIDENCE
        result = classify_finding(finding, SAMPLE_DIFF)
        assert result.is_fixable is True
        assert result.confidence == float(DEFAULT_MIN_CONFIDENCE)

    def test_confidence_threshold_configurable_strict(self):
        """Raising min_confidence to 90 blocks patterns below that threshold."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=12,
            severity="minor",
            issue="Missing null check",
            suggestion="Add if not username check",
            confidence=0.60,
            category="code-quality-expert"
        )
        # missing_null_check confidence is 70 — below strict threshold of 90
        result = classify_finding(finding, SAMPLE_DIFF, min_confidence=90)
        assert result.is_fixable is False
        assert result.confidence == 70.0

    def test_confidence_threshold_configurable_strict_high_confidence_pattern(self):
        """At min_confidence=90, hardcoded_secret (confidence 90) is still fixable."""
        finding = AIReview(
            file_path="app/auth.py",
            line_number=11,
            severity="critical",
            issue="Hardcoded API key detected",
            suggestion="Move to environment variable",
            confidence=0.95,
            category="security-analyst"
        )
        result = classify_finding(finding, SAMPLE_DIFF, min_confidence=90)
        assert result.is_fixable is True
        assert result.confidence == 90.0


class TestFixabilityResult:
    """Test FixabilityResult data model."""
    
    def test_fixability_result_creation(self):
        """FixabilityResult should be created with all fields."""
        result = FixabilityResult(
            is_fixable=True,
            confidence=85.0,
            category="self-contained",
            reason="matched hardcoded secret pattern"
        )
        
        assert result.is_fixable is True
        assert result.confidence == 85.0
        assert result.category == "self-contained"
        assert result.reason == "matched hardcoded secret pattern"
