#!/usr/bin/env python3
"""
Sage Fixability Classifier — Pattern-based heuristics for auto-fix classification.

Categorizes code review findings as:
- self-contained (fixable by Sage)
- context-dependent (needs human review)
- unfixable (finding line not in diff or lacks actionable info)

No AI calls — fast, deterministic pattern matching.

TODO (future):
- Precompile regex patterns for performance (currently minor, but matters at scale)
- Support context diff format (currently assumes unified diff)
- Add telemetry for false positive tracking
- Consider reusing diff_parser.py logic for diff parsing
"""

import re
from typing import Optional
from .models import AIReview, FixabilityResult


# Pattern registry for known auto-fixable issues
FIXABLE_PATTERNS = {
    # Security patterns (high confidence)
    "hardcoded_secret": {
        "patterns": [
            r'hardcoded.*(?:api.?key|password|secret|token)',
            r'api[_-]?key\s*=\s*["\'][^"\']+["\']',
            r'password\s*=\s*["\'][^"\']+["\']',
            r'secret\s*=\s*["\'][^"\']+["\']',
            r'token\s*=\s*["\'][^"\']+["\']',
            r'aws[_-]?access[_-]?key[_-]?id\s*=',
        ],
        "confidence": 90,
        "category": "self-contained",
    },
    "sql_injection": {
        "patterns": [
            r'sql.?injection',
            r'execute\s*\(\s*["\'].*\+.*["\']',  # String concatenation in SQL
            r'query\s*\(\s*f["\']',              # f-string in SQL query
            r'\.raw\s*\(\s*f["\']',              # Django raw() with f-string
            r'f["\'].*select.*from',             # f-string with SELECT
        ],
        "confidence": 85,
        "category": "self-contained",
    },
    
    # Code quality patterns (medium-high confidence)
    "unused_import": {
        "patterns": [
            r'import\s+\w+\s+#.*unused',
            r'from\s+\w+\s+import\s+\w+\s+#.*unused',
        ],
        "confidence": 80,
        "category": "self-contained",
    },
    "missing_null_check": {
        "patterns": [
            r'if\s+\w+\s*==\s*None',           # Simple null check suggestion
            r'if\s+not\s+\w+',                  # Truthy check
        ],
        "confidence": 70,
        "category": "self-contained",
    },
    "typo_in_string": {
        "patterns": [
            r'#.*typo',
            r'#.*misspell',
        ],
        "confidence": 75,
        "category": "self-contained",
    },
}

# Agent-specific classification rules
AGENT_RULES = {
    "architecture-reviewer": {
        "default_category": "context-dependent",
        "default_confidence": 90,
        "reason": "architectural findings require broader context",
    },
    "performance-expert": {
        "default_category": "context-dependent",
        "default_confidence": 85,
        "reason": "performance issues often need profiling data",
    },
}


def _is_line_in_diff(file_path: str, line_number: int, diff: str) -> bool:
    """
    Check if a specific line is part of the changed code in the diff.
    
    Returns True if the line is in a new/modified section (+ lines).
    """
    if not diff:
        return False
    
    # Parse diff to find the file section
    in_file = False
    current_new_line = 0
    
    for line in diff.split('\n'):
        # Check for new file marker (reset state for multi-file diffs)
        if line.startswith('diff --git'):
            in_file = False
            current_new_line = 0
        
        # Check for file marker
        if line.startswith('+++') and file_path in line:
            in_file = True
            continue
        
        if in_file:
            # Check for hunk header (@@ -old_start,old_count +new_start,new_count @@)
            if line.startswith('@@'):
                match = re.match(r'@@ -\d+,?\d* \+(\d+),?\d* @@', line)
                if match:
                    current_new_line = int(match.group(1))
                continue
            
            # Track line numbers (only in new file side)
            if line.startswith('+') and not line.startswith('+++'):
                if current_new_line == line_number:
                    return True
                current_new_line += 1
            elif not line.startswith('-'):
                # Context line (both old and new)
                current_new_line += 1
    
    return False


def _match_fixable_patterns(finding: AIReview, file_content: str = "") -> Optional[dict]:
    """
    Match finding against known fixable patterns.
    
    Returns pattern metadata if match found, None otherwise.
    """
    # Check issue text and suggestion for pattern matches
    text_to_check = f"{finding.issue} {finding.suggestion}".lower()
    
    for pattern_type, pattern_data in FIXABLE_PATTERNS.items():
        for pattern in pattern_data["patterns"]:
            if re.search(pattern, text_to_check, re.IGNORECASE):
                return pattern_data
            
            # Also check file content around the line if available
            if file_content:
                lines = file_content.split('\n')
                if 0 <= finding.line_number - 1 < len(lines):
                    line_content = lines[finding.line_number - 1]
                    if re.search(pattern, line_content, re.IGNORECASE):
                        return pattern_data
    
    return None


def _apply_agent_rules(finding: AIReview) -> Optional[dict]:
    """
    Apply agent-specific classification rules.
    
    Returns rule metadata if agent-specific rule applies, None otherwise.
    """
    category = finding.category if hasattr(finding, 'category') else "general"
    
    if category in AGENT_RULES:
        return AGENT_RULES[category]
    
    return None


def classify_finding(
    finding: AIReview,
    diff: str,
    file_content: str = ""
) -> FixabilityResult:
    """
    Classify if a code review finding can be auto-fixed by Sage.
    
    Args:
        finding: The AI review finding to classify
        diff: The full diff string (unified diff format)
        file_content: Optional full file content for pattern matching
    
    Returns:
        FixabilityResult with classification, confidence, and reasoning
    
    Classification logic:
    1. If finding line not in diff → unfixable (100% confidence)
    2. If matches known fixable pattern → self-contained (pattern confidence)
    3. If agent-specific rule applies → apply rule
    4. Default → context-dependent (50% confidence)
    
    Only marks is_fixable=True if confidence >= 70.
    """
    # Step 1: Check if line is in the diff
    if not _is_line_in_diff(finding.file_path, finding.line_number, diff):
        return FixabilityResult(
            is_fixable=False,
            confidence=100.0,
            category="unfixable",
            reason="finding line not in diff (unchanged code)"
        )
    
    # Step 2: Check for known fixable patterns
    pattern_match = _match_fixable_patterns(finding, file_content)
    if pattern_match:
        confidence = pattern_match["confidence"]
        return FixabilityResult(
            is_fixable=(confidence >= 70),
            confidence=float(confidence),
            category=pattern_match["category"],
            reason="matched known fixable pattern"
        )
    
    # Step 3: Apply agent-specific rules
    agent_rule = _apply_agent_rules(finding)
    if agent_rule:
        confidence = agent_rule["default_confidence"]
        return FixabilityResult(
            is_fixable=False,  # Agent rules are for context-dependent findings
            confidence=float(confidence),
            category=agent_rule["default_category"],
            reason=agent_rule["reason"]
        )
    
    # Step 4: Default to context-dependent
    return FixabilityResult(
        is_fixable=False,
        confidence=50.0,
        category="context-dependent",
        reason="no clear fixable pattern detected"
    )
