#!/usr/bin/env python3
"""
Sage Fix Generator — AI-powered code fix generation.

Generates concrete code fixes for classified fixable findings.
Uses AIClient to produce minimal, targeted fixes.

TODO (future):
- Multi-round validation (generate → test → refine)
- Language-specific fix templates
- Fix verification against test suite
"""

import json
from typing import Optional
from .models import AIReview, CodeFix
from .ai_client import AIClient


FIX_GENERATION_PROMPT = """You are Sage, an AI code fix generator. Your job is to produce MINIMAL, SAFE code fixes for specific issues.

**Rules:**
1. Fix ONLY the specific issue mentioned — do not refactor surrounding code
2. Preserve formatting, style, and variable names unless they're part of the issue
3. Return a valid JSON object with the fix
4. If you cannot produce a safe fix, set confidence to 0

**Finding to fix:**
File: {file_path}
Line: {line_number}
Severity: {severity}
Issue: {issue}
Suggestion: {suggestion}

**Current code (lines {start_line}-{end_line}):**
```
{code_snippet}
```

**Full file context (if needed):**
```
{file_content}
```

**Diff context (shows what changed in this PR):**
```
{diff_snippet}
```

Return ONLY a JSON object in this exact format:
{{
  "fixed_lines": ["line 1", "line 2", ...],
  "confidence": 85,
  "explanation": "Changed X to Y because Z"
}}

If you cannot safely fix this issue, return:
{{
  "fixed_lines": [],
  "confidence": 0,
  "explanation": "Reason why this cannot be auto-fixed"
}}
"""


def generate_fix(
    finding: AIReview,
    file_content: str,
    diff: str,
    ai_client: AIClient,
    context_lines: int = 3
) -> Optional[CodeFix]:
    """
    Generate a code fix for a classified fixable finding.
    
    Args:
        finding: The AIReview finding to fix
        file_content: Full file content
        diff: The diff string (for context)
        ai_client: AIClient instance for LLM calls
        context_lines: Number of lines before/after to include for context
    
    Returns:
        CodeFix if successful, None if fix cannot be generated
    
    Raises:
        ValueError: If finding line is out of bounds
    """
    lines = file_content.split('\n')
    line_idx = finding.line_number - 1  # Convert to 0-indexed
    
    if line_idx < 0 or line_idx >= len(lines):
        raise ValueError(f"Line {finding.line_number} out of bounds (file has {len(lines)} lines)")
    
    # Extract code snippet with context
    start_line = max(0, line_idx - context_lines)
    end_line = min(len(lines), line_idx + context_lines + 1)
    code_snippet = '\n'.join(lines[start_line:end_line])
    
    # Extract relevant diff snippet (just the file section)
    diff_snippet = _extract_file_diff(finding.file_path, diff)
    
    # Build prompt
    prompt = FIX_GENERATION_PROMPT.format(
        file_path=finding.file_path,
        line_number=finding.line_number,
        severity=finding.severity,
        issue=finding.issue,
        suggestion=finding.suggestion,
        start_line=start_line + 1,  # Back to 1-indexed for display
        end_line=end_line,
        code_snippet=code_snippet,
        file_content=file_content[:2000],  # Limit context to avoid token overflow
        diff_snippet=diff_snippet[:1000]
    )
    
    # Call AI
    try:
        response = ai_client.generate(prompt, max_tokens=1000, temperature=0.2)
        
        # Parse JSON response
        # AI might wrap in markdown code blocks, strip those
        response_text = response.strip()
        if response_text.startswith('```'):
            # Extract content between ```json and ```
            lines = response_text.split('\n')
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith('```'):
                    if in_block:
                        break
                    in_block = True
                    continue
                if in_block:
                    json_lines.append(line)
            response_text = '\n'.join(json_lines)
        
        result = json.loads(response_text)
        
        # Validate response structure
        if 'fixed_lines' not in result or 'confidence' not in result or 'explanation' not in result:
            return None
        
        # Validate confidence bounds
        confidence = float(result['confidence'])
        if confidence < 0 or confidence > 100:
            return None
        
        # Check if AI declined to fix
        if confidence == 0 or not result['fixed_lines']:
            return None
        
        # Build CodeFix
        original_lines = lines[start_line:end_line]
        fixed_lines = result['fixed_lines']
        
        return CodeFix(
            original_lines=original_lines,
            fixed_lines=fixed_lines,
            start_line=start_line + 1,  # Back to 1-indexed
            end_line=end_line,
            confidence=confidence,
            explanation=result['explanation']
        )
        
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # AI response malformed or invalid
        return None


def _extract_file_diff(file_path: str, diff: str) -> str:
    """
    Extract just the diff section for a specific file.
    
    Args:
        file_path: Path to extract
        diff: Full diff string
    
    Returns:
        Diff snippet for the file, or empty string if not found
    """
    if not diff:
        return ""
    
    lines = diff.split('\n')
    file_lines = []
    in_file = False
    
    for line in lines:
        # Check for new file marker
        if line.startswith('diff --git'):
            in_file = False
        
        # Check if this is our file
        if line.startswith('+++') and file_path in line:
            in_file = True
            file_lines.append(line)
            continue
        
        if in_file:
            file_lines.append(line)
            # Stop at next file
            if line.startswith('diff --git'):
                break
    
    return '\n'.join(file_lines)
