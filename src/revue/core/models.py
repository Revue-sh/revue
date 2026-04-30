#!/usr/bin/env python3
"""
Core data models for Revue.

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
    language: str = "unknown"  # Detected from file extension by diff_parser.detect_language()


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
    agent_name: str = ""  # name of the agent that produced this finding (e.g. "maya")
    synthesised_from: list[tuple[str, str]] | None = None  # (agent_name, category) pairs for synthesis (e.g., [("kai", "performance"), ("zara", "security")])
    code_replacement: list[str] | None = None  # exact replacement lines for GitHub/GitLab suggestion blocks
    replacement_line_count: int = 1  # number of original source lines the code_replacement covers (REVUE-201)


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


@dataclass
class PRContext:
    """VCS context for a pull/merge request.

    Passed as a single optional parameter to ReviewPipeline.run() instead of
    individual platform/pr_number/repo_* kwargs (OCP — adding new PR metadata
    fields does not require changing the pipeline signature).
    """
    platform: str           # "bitbucket" | "github" | "gitlab"
    pr_number: int
    repo_owner: str
    repo_name: str
    repo_path: str          # local repo root for .revue.yml and comment store


@dataclass
class ClassificationResult:
    """Result of the zero-side-effect classify phase (REVUE-112 Phase 2, AC15).

    Produced by WontFixReplyService.classify() without any file writes, API
    POSTs, or store mutations (AC21).  Pass to WontFixReplyService.respond()
    for all I/O: lessons PR creation, thread replies, store state updates.
    """
    patterns_to_allow: list[dict]       # entries for noise_filters.allowed_patterns
    patterns_to_disallow: list[dict]    # entries for noise_filters.disallowed_patterns
    state_updates: list[dict]           # {fingerprint, file_path, decision} — resolved threads
    decisions: list[dict]               # raw AI output, carried through to respond()
