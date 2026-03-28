#!/usr/bin/env python3
"""
Tests for Sage fix generator.
"""

import pytest
import json
from unittest.mock import Mock, MagicMock
from revue.core.sage_generator import (
    generate_fix,
    _extract_file_diff,
)
from revue.core.models import AIReview, CodeFix


# Sample file content for testing
SAMPLE_FILE_CONTENT = """def authenticate(username, password):
    # Authentication logic
    api_key = "sk-1234567890abcdef"
    
    if not username:
        return None
    
    return validate_credentials(username, password)
"""

# Sample diff
SAMPLE_DIFF = """diff --git a/app/auth.py b/app/auth.py
index 1234567..89abcdef 100644
--- a/app/auth.py
+++ b/app/auth.py
@@ -2,3 +2,3 @@ def authenticate(username, password):
     # Authentication logic
-    api_key = "sk-1234567890abcdef"
+    api_key = os.getenv("API_KEY")
"""


class TestExtractFileDiff:
    """Test diff extraction helper."""
    
    def test_extract_file_diff(self):
        """Should extract diff section for specific file."""
        result = _extract_file_diff("app/auth.py", SAMPLE_DIFF)
        
        assert "+++ b/app/auth.py" in result
        assert 'api_key = os.getenv("API_KEY")' in result
    
    def test_extract_file_diff_not_found(self):
        """Should return empty string if file not in diff."""
        result = _extract_file_diff("app/other.py", SAMPLE_DIFF)
        assert result == ""
    
    def test_extract_file_diff_empty(self):
        """Should handle empty diff gracefully."""
        result = _extract_file_diff("app/auth.py", "")
        assert result == ""


class TestGenerateFix:
    """Test fix generation logic."""
    
    def test_generate_fix_success(self):
        """Should generate fix with valid AI response."""
        # Mock AIClient
        mock_client = Mock()
        mock_client.generate = Mock(return_value=json.dumps({
            "fixed_lines": [
                '    api_key = os.getenv("API_KEY")',
            ],
            "confidence": 90,
            "explanation": "Moved hardcoded secret to environment variable"
        }))
        
        finding = AIReview(
            file_path="app/auth.py",
            line_number=3,
            severity="critical",
            issue="Hardcoded API key detected",
            suggestion="Move to environment variable",
            confidence=0.95,
            category="security-analyst"
        )
        
        result = generate_fix(finding, SAMPLE_FILE_CONTENT, SAMPLE_DIFF, mock_client)
        
        assert result is not None
        assert isinstance(result, CodeFix)
        assert result.confidence == 90.0
        assert "environment variable" in result.explanation.lower()
        assert len(result.fixed_lines) == 1
        assert 'os.getenv("API_KEY")' in result.fixed_lines[0]
    
    def test_generate_fix_with_markdown_wrapper(self):
        """Should handle AI response wrapped in markdown code blocks."""
        mock_client = Mock()
        mock_client.generate = Mock(return_value="""```json
{
    "fixed_lines": ["    api_key = os.getenv(\\"API_KEY\\")"],
    "confidence": 85,
    "explanation": "Replaced hardcoded secret"
}
```""")
        
        finding = AIReview(
            file_path="app/auth.py",
            line_number=3,
            severity="critical",
            issue="Hardcoded API key",
            suggestion="Use env var",
            confidence=0.90,
            category="security-analyst"
        )
        
        result = generate_fix(finding, SAMPLE_FILE_CONTENT, SAMPLE_DIFF, mock_client)
        
        assert result is not None
        assert result.confidence == 85.0
    
    def test_generate_fix_ai_declines(self):
        """Should return None if AI confidence is 0."""
        mock_client = Mock()
        mock_client.generate = Mock(return_value=json.dumps({
            "fixed_lines": [],
            "confidence": 0,
            "explanation": "Cannot safely fix without more context"
        }))
        
        finding = AIReview(
            file_path="app/auth.py",
            line_number=3,
            severity="major",
            issue="Complex refactoring needed",
            suggestion="Unclear how to proceed",
            confidence=0.50,
            category="architecture-reviewer"
        )
        
        result = generate_fix(finding, SAMPLE_FILE_CONTENT, SAMPLE_DIFF, mock_client)
        
        assert result is None
    
    def test_generate_fix_malformed_json(self):
        """Should return None if AI response is malformed JSON."""
        mock_client = Mock()
        mock_client.generate = Mock(return_value="This is not valid JSON {}")
        
        finding = AIReview(
            file_path="app/auth.py",
            line_number=3,
            severity="minor",
            issue="Some issue",
            suggestion="Some fix",
            confidence=0.70,
            category="code-quality-expert"
        )
        
        result = generate_fix(finding, SAMPLE_FILE_CONTENT, SAMPLE_DIFF, mock_client)
        
        assert result is None
    
    def test_generate_fix_missing_fields(self):
        """Should return None if AI response missing required fields."""
        mock_client = Mock()
        mock_client.generate = Mock(return_value=json.dumps({
            "fixed_lines": ["some code"],
            # Missing confidence and explanation
        }))
        
        finding = AIReview(
            file_path="app/auth.py",
            line_number=3,
            severity="minor",
            issue="Issue",
            suggestion="Fix",
            confidence=0.70,
            category="code-quality-expert"
        )
        
        result = generate_fix(finding, SAMPLE_FILE_CONTENT, SAMPLE_DIFF, mock_client)
        
        assert result is None
    
    def test_generate_fix_line_out_of_bounds(self):
        """Should raise ValueError if finding line is out of bounds."""
        mock_client = Mock()
        
        finding = AIReview(
            file_path="app/auth.py",
            line_number=999,  # Way beyond file length
            severity="minor",
            issue="Issue",
            suggestion="Fix",
            confidence=0.70,
            category="code-quality-expert"
        )
        
        with pytest.raises(ValueError, match="out of bounds"):
            generate_fix(finding, SAMPLE_FILE_CONTENT, SAMPLE_DIFF, mock_client)
    
    def test_generate_fix_preserves_context(self):
        """Should include context lines in original_lines."""
        mock_client = Mock()
        mock_client.generate = Mock(return_value=json.dumps({
            "fixed_lines": [
                "    # Authentication logic",
                '    api_key = os.getenv("API_KEY")',
                "    ",
            ],
            "confidence": 88,
            "explanation": "Fixed the secret"
        }))
        
        finding = AIReview(
            file_path="app/auth.py",
            line_number=3,
            severity="critical",
            issue="Hardcoded secret",
            suggestion="Use env var",
            confidence=0.92,
            category="security-analyst"
        )
        
        result = generate_fix(finding, SAMPLE_FILE_CONTENT, SAMPLE_DIFF, mock_client, context_lines=2)
        
        assert result is not None
        assert len(result.original_lines) > 1  # Should include context
        assert result.start_line == 1  # Line 3 - 2 context = 1
    
    def test_code_fix_dataclass(self):
        """CodeFix should be created with all fields."""
        fix = CodeFix(
            original_lines=["old line 1", "old line 2"],
            fixed_lines=["new line 1", "new line 2"],
            start_line=10,
            end_line=12,
            confidence=85.5,
            explanation="Replaced X with Y"
        )
        
        assert fix.original_lines == ["old line 1", "old line 2"]
        assert fix.fixed_lines == ["new line 1", "new line 2"]
        assert fix.start_line == 10
        assert fix.end_line == 12
        assert fix.confidence == 85.5
        assert fix.explanation == "Replaced X with Y"
    
    def test_generate_fix_invalid_confidence(self):
        """Should return None if AI confidence is out of bounds."""
        mock_client = Mock()
        mock_client.generate = Mock(return_value=json.dumps({
            "fixed_lines": ["some code"],
            "confidence": 150,  # Invalid — must be 0-100
            "explanation": "Fixed it"
        }))
        
        finding = AIReview(
            file_path="app/auth.py",
            line_number=3,
            severity="minor",
            issue="Issue",
            suggestion="Fix",
            confidence=0.70,
            category="code-quality-expert"
        )
        
        result = generate_fix(finding, SAMPLE_FILE_CONTENT, SAMPLE_DIFF, mock_client)
        
        assert result is None
