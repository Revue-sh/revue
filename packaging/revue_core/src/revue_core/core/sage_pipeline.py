#!/usr/bin/env python3
"""
Sage Pipeline — orchestrates fixability classification, fix generation, and VCS posting.

Designed to be called after Nova consolidation in the main review pipeline.
"""

from typing import Protocol
from dataclasses import dataclass
from .models import AIReview, CodeFix, FixabilityResult
from .sage_classifier import classify_finding
from .sage_generator import generate_fix
from .ai_client import AIClient
from .vcs_adapter import DiffPosition


@dataclass
class SageSummary:
    """Summary of Sage's auto-fix analysis."""
    auto_fixable_count: int
    manual_review_count: int
    unfixable_count: int
    posted_suggestions_count: int
    total_findings: int
    
    def to_markdown(self) -> str:
        """Generate markdown summary for review output."""
        if self.total_findings == 0:
            return "🤖 **Sage Analysis:** No findings to review."
        
        sections = []
        
        if self.auto_fixable_count > 0:
            sections.append(
                f"🔧 **Auto-fixable:** {self.auto_fixable_count} issue(s) "
                f"({self.posted_suggestions_count} suggestion(s) posted — click to apply)"
            )
        
        if self.manual_review_count > 0:
            sections.append(
                f"⚠️ **Needs manual review:** {self.manual_review_count} issue(s)"
            )
        
        if self.unfixable_count > 0:
            sections.append(
                f"❌ **Unfixable:** {self.unfixable_count} issue(s) (code not in diff or insufficient context)"
            )
        
        return "\n".join(sections)


class VCSAdapter(Protocol):
    """Protocol for VCS adapters (GitHub, GitLab)."""
    
    def post_suggested_change(
        self, pr_id: int, position: DiffPosition, code_fix: CodeFix
    ) -> bool:
        """Post a suggested change (GitHub-style)."""
        ...
    
    def post_apply_suggestion(
        self, pr_id: int, position: DiffPosition, code_fix: CodeFix
    ) -> bool:
        """Post an apply suggestion (GitLab-style)."""
        ...
    
    def resolve_position(
        self, file_path: str, line_number: int, diff: str
    ) -> DiffPosition:
        """Resolve a line number to a VCS-specific position."""
        ...


def run_sage_analysis(
    findings: list[AIReview],
    diff: str,
    file_contents: dict[str, str],
    ai_client: AIClient,
    vcs_adapter: VCSAdapter,
    pr_id: int,
    platform: str = "github"
) -> SageSummary:
    """
    Run full Sage analysis on consolidated findings.
    
    Steps:
    1. Classify each finding (fixable, context-dependent, unfixable)
    2. Generate fixes for fixable findings
    3. Post suggestions via VCS adapter
    4. Build summary
    
    Args:
        findings: Consolidated findings from Nova
        diff: Full unified diff string
        file_contents: Dict mapping file_path → file content
        ai_client: AIClient for fix generation
        vcs_adapter: VCS adapter (GitHub or GitLab)
        pr_id: Pull/Merge request ID
        platform: "github" or "gitlab" (determines which posting method to use)
    
    Returns:
        SageSummary with counts and statistics
    """
    auto_fixable_count = 0
    manual_review_count = 0
    unfixable_count = 0
    posted_suggestions_count = 0
    
    for finding in findings:
        # Step 1: Classify
        file_content = file_contents.get(finding.file_path, "")
        classification = classify_finding(finding, diff, file_content)
        
        if classification.category == "unfixable":
            unfixable_count += 1
            continue
        
        if classification.category == "context-dependent" or not classification.is_fixable:
            manual_review_count += 1
            continue
        
        # Step 2: Generate fix
        auto_fixable_count += 1
        
        try:
            code_fix = generate_fix(finding, file_content, diff, ai_client)
        except Exception:
            # Generation failed — count as manual review
            manual_review_count += 1
            auto_fixable_count -= 1
            continue
        
        if code_fix is None:
            # AI declined to fix — count as manual review
            manual_review_count += 1
            auto_fixable_count -= 1
            continue
        
        # Step 3: Post suggestion
        position = vcs_adapter.resolve_position(
            finding.file_path,
            finding.line_number,
            diff
        )
        
        try:
            if platform == "gitlab":
                success = vcs_adapter.post_apply_suggestion(pr_id, position, code_fix)
            else:  # github
                success = vcs_adapter.post_suggested_change(pr_id, position, code_fix)
            
            if success:
                posted_suggestions_count += 1
        except Exception:
            # Posting failed — log but don't fail the whole analysis
            pass
    
    return SageSummary(
        auto_fixable_count=auto_fixable_count,
        manual_review_count=manual_review_count,
        unfixable_count=unfixable_count,
        posted_suggestions_count=posted_suggestions_count,
        total_findings=len(findings)
    )
