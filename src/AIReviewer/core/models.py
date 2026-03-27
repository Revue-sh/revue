#!/usr/bin/env python3
"""
Core data models for AI Code Review Service.

This module contains the shared data structures used across all review modes.
"""

from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    """Severity levels for code review findings."""
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    SUGGESTION = "suggestion"


@dataclass
class FileChange:
    """Represents a file change in a merge request."""
    file_path: str
    change_type: str
    additions: int
    deletions: int
    diff: str


@dataclass
class AIReview:
    """Represents a single AI-generated code review finding."""
    file_path: str
    line_number: int
    severity: str
    issue: str
    suggestion: str
    confidence: float
    category: str = "general"


@dataclass
class EnhancedIssue:
    """Enhanced issue representation with semantic fingerprinting."""
    text: str
    line_number: int
    severity: str
    file_path: str = ""
    semantic_fingerprint: str = ""
    content_hash: str = ""
    surrounding_context: list[str] = field(default_factory=list)
    symbols_mentioned: list[str] = field(default_factory=list)
    function_context: str = ""
    confidence: float = 0.0


@dataclass
class FixabilityResult:
    """Result of Sage fixability classification."""
    is_fixable: bool
    confidence: float  # 0-100
    category: str      # "self-contained" | "context-dependent" | "unfixable"
    reason: str


@dataclass
class CodeFix:
    """Generated code fix from Sage."""
    original_lines: list[str]  # Lines before fix
    fixed_lines: list[str]     # Lines after fix
    start_line: int            # First line number (1-indexed)
    end_line: int              # Last line number (inclusive)
    confidence: float          # 0-100
    explanation: str           # Why this fix works
