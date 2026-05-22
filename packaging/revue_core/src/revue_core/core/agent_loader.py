"""
Agent definition loader — parse YAML/Markdown agent definition files (Story [016]).

SRP: loading/parsing only. Agent execution is in agent_runner.py.
OCP: new agent definition formats can be added by implementing AgentDefinitionParser Protocol.
DIP: AgentRunner depends on AgentProtocol, not concrete loaded agent classes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from .ai_client import AIClient, CompletionResult
    from .ai_config import AIConfig
    from .shared_analysis import SharedAnalysisResult
    from .tools.read_file import ReadFileTool

from .ai_client import _CACHE_CONTROL_1H
from .diff_parser import detect_language
from .models import FileChange, AIReview

from revue_core.core.logging_channels import Log


# REVUE-244: guard_rails prepending location. The shared file is looked up
# relative to each agent's own directory (so production and dogfood _revue/agents
# both pick up the same content from their own _shared/ subdir). The default
# below is the production path used when no agent directory is available
# (e.g., legacy callers that pass only a system_prompt string).
_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
_SHARED_GUARD_RAILS_PATH = _AGENTS_DIR / "_shared" / "guard_rails.md"

# Four canonical category values that cli._CATEGORY_MAP recognises.
_KNOWN_CATEGORIES: frozenset[str] = frozenset({
    "architecture", "security", "performance", "code-quality"
})

# Fallback canonical category keyed by agent definition name.
# Used when the AI omits the category field or returns an unrecognised string,
# preventing agent names from leaking into the summary Quality Breakdown.
_AGENT_CANONICAL_CATEGORY: dict[str, str] = {
    "leo": "architecture",
    "zara": "security",
    "kai": "performance",
    "maya": "code-quality",
}

_REVIEW_INSTRUCTIONS = (
    # REVUE-246 three-state contract: every reviewer turn ends with exactly one
    # of the three top-level shapes below. The discriminator is ``status``; the
    # output_config grammar enforces exclusivity at the API boundary.
    "Respond with one of the three JSON shapes below — never plain prose, "
    "never the legacy bare-array shape. Output valid JSON only: no markdown "
    "fences, no inline comments, no trailing commas.\n"
    "\n"
    "1) FINDINGS — at least one issue flagged:\n"
    "{\n"
    '  "status": "findings",\n'
    '  "findings": [\n'
    "    {\n"
    '      "file_path": "src/example",\n'
    '      "line_number": 42,\n'
    '      "severity": "high|medium|low|info",\n'
    '      "issue": "One sentence naming the problem.",\n'
    '      "suggestion": "One sentence fix.",\n'
    '      "confidence": 0.85,\n'
    '      "category": "architecture|security|performance|code-quality",\n'
    '      "code_replacement": ["replacement line 1", "line 2"],\n'
    '      "replacement_line_count": 2\n'
    "    }\n"
    "  ],\n"
    '  "summary": "optional one-line summary of what you reviewed"\n'
    "}\n"
    "\n"
    "2) CLEAN — reviewed the diff and found nothing of concern:\n"
    "{\n"
    '  "status": "clean",\n'
    '  "summary": "REQUIRED — one sentence saying what you actually reviewed",\n'
    '  "confidence": 0.85\n'
    "}\n"
    "Use this ONLY when you have actually walked the diff and have nothing "
    "to flag. Never use clean as a way to exit early when overwhelmed — emit "
    "error(max_iterations_no_verdict) instead.\n"
    "\n"
    "3) ERROR — you cannot produce a verdict:\n"
    "{\n"
    '  "status": "error",\n'
    '  "error": {\n'
    '    "code": "tool_unavailable|model_refusal|internal_error",\n'
    '    "message": "one sentence explaining why no verdict was possible",\n'
    '    "iterations_used": 0\n'
    "  }\n"
    "}\n"
    "Use error when your tools failed repeatedly (after fallback to diff-only), "
    "when the request is something you cannot answer, or when something else "
    "blocks a real verdict. NEVER emit a silent empty findings array.\n"
    "\n"
    "Field rules for findings:\n"
    "- confidence: float 0.0–1.0 reflecting how certain you are this is a real issue.\n"
    "- suggestion: prose description of the fix. NEVER include inline code examples — all "
    "code belongs exclusively in code_replacement. The suggestion should be clear and "
    "actionable without code.\n"
    "- code_replacement: when you can provide exact verbatim replacement lines for a "
    "single-location fix, set this to an array of strings — one string per source line, "
    "no line numbers, no integers, no nulls inside the array. IMPORTANT: code_replacement "
    "must be a complete and working replacement consistent with the suggestion. Omit "
    "code_replacement entirely if it would be partial or incomplete (e.g., building a list "
    "but omitting the join). Leave it null when the fix requires broader context or is "
    "descriptive only.\n"
    "- replacement_line_count: when code_replacement is provided, set this to the number "
    "of original source lines being replaced (default 1). For example, if you are replacing "
    "a function signature that spans 3 lines, set this to 3."
)

def filter_code_replacement(raw: object) -> "list[str] | None":
    """Return a sanitised list of string lines from an AI-supplied code_replacement value.

    Filters out any non-string items (integers, nulls) that the AI may return.
    Escapes triple-backtick sequences only when they appear at the START of a line —
    those would close the surrounding suggestion fence prematurely. Backticks that
    appear mid-line are safe and are left verbatim so committed code is not corrupted.
    Returns None when the result would be empty.
    """
    if not isinstance(raw, list):
        return None
    def _escape(line: str) -> str:
        if line.lstrip().startswith("```"):
            return line.replace("```", "~~~", 1)
        return line
    lines = [_escape(l) for l in raw if isinstance(l, str)]
    return lines or None


_SEV_MAP: dict[str, str] = {
    "critical": "high",
    "major": "medium",
    "minor": "low",
    "suggestion": "info",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "info",
}


# ---------------------------------------------------------------------------
# Agent definition dataclass
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# REVUE-246: typed reviewer-verdict result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentVerdict:
    """Outcome of a single reviewer's analysis.

    Status is the closed-set discriminator: ``findings`` / ``clean`` / ``error``.
    The dataclass is iterable + len-able + subscriptable over its ``findings``
    so legacy callers that treated the old ``list[AIReview]`` return value as
    an iterable keep working — new callers should branch on ``status``.
    """
    status: str
    findings: list[AIReview] = field(default_factory=list)
    summary: "str | None" = None
    confidence: "float | None" = None
    error_code: "str | None" = None
    error_message: "str | None" = None
    iterations_used: "int | None" = None

    def __iter__(self):  # type: ignore[override]
        return iter(self.findings)

    def __len__(self) -> int:
        return len(self.findings)

    def __getitem__(self, idx):  # type: ignore[no-untyped-def]
        return self.findings[idx]

    @property
    def is_findings(self) -> bool:
        return self.status == "findings"

    @property
    def is_clean(self) -> bool:
        return self.status == "clean"

    @property
    def is_error(self) -> bool:
        return self.status == "error"

    @classmethod
    def from_findings(
        cls,
        findings: list[AIReview],
        summary: "str | None" = None,
    ) -> "AgentVerdict":
        return cls(status="findings", findings=findings, summary=summary)

    @classmethod
    def from_clean(cls, summary: str, confidence: float) -> "AgentVerdict":
        return cls(status="clean", summary=summary, confidence=confidence)

    @classmethod
    def from_error(
        cls,
        code: str,
        message: str,
        iterations_used: "int | None" = None,
    ) -> "AgentVerdict":
        return cls(
            status="error",
            error_code=code,
            error_message=message,
            iterations_used=iterations_used,
        )


@dataclass
class AgentDefinition:
    """Parsed agent definition from YAML or Markdown front-matter."""
    name: str                           # e.g. "zara"
    display_name: str                   # e.g. "Zara (Security Analyst)"
    role: str                           # one-liner role description
    system_prompt: str                  # full system prompt for the AI call
    focus_areas: list[str] = field(default_factory=list)
    trigger_patterns: list[str] = field(default_factory=list)  # fnmatch patterns to trigger
    # Short noun phrase naming the agent's domain (e.g. "performance
    # engineering", "application security"). Read by language_injection to
    # build per-agent priming alongside the repository's primary language.
    # Empty for custom agents that don't declare it — falls back to a
    # language-only priming with no domain axis.
    expertise: str = ""
    severity_default: str = "minor"
    enabled: bool = True
    version: str = "1.0"
    # REVUE-241 Gap 2: per-agent tool-use budget. Different reviewers need
    # different amounts of read_file exploration before they can synthesise
    # findings — Leo's architecture review of a large diff legitimately needs
    # more iterations than Kai's narrow performance pass. Default is the
    # historical 5; override in the agent's YAML/MD front-matter when its
    # scope warrants more (e.g. Leo on cross-cutting changes).
    max_tool_iterations: int = 5


_REPLACEMENT_LINE_COUNT_MAX = 100


def _parse_finding_item(
    item: object,
    agent_name: str,
    severity_default: str = "minor",
) -> "AIReview | None":
    """Parse one raw finding dict from an AI response into an AIReview.

    Returns None for non-dict items; callers should skip None entries.
    """
    if not isinstance(item, dict):
        return None

    raw_sev = item.get("severity", severity_default)
    if not isinstance(raw_sev, str):
        raw_sev = severity_default
    severity = _SEV_MAP.get(raw_sev.lower(), "low")

    code_replacement = filter_code_replacement(item.get("code_replacement"))

    # replacement_line_count: accept int or float (LLMs often emit 3.0), cap at max,
    # and coerce to 1 when no replacement is provided.
    replacement_line_count = 1
    if code_replacement is not None:
        try:
            candidate = item.get("replacement_line_count")
            if (
                isinstance(candidate, (int, float))
                and not isinstance(candidate, bool)
                and candidate > 0
            ):
                replacement_line_count = min(int(candidate), _REPLACEMENT_LINE_COUNT_MAX)
        except (TypeError, ValueError):
            pass

    file_path = item.get("file_path", "unknown")
    return AIReview(
        file_path=file_path,
        line_number=int(item.get("line_number", 0)),
        severity=severity,
        issue=item.get("issue", ""),
        suggestion=item.get("suggestion", ""),
        confidence=float(item.get("confidence", 0.7)),
        category=_normalise_category(
            item.get("category", "") if isinstance(item.get("category", ""), str) else "",
            agent_name,
        ),
        agent_name=agent_name,
        code_replacement=code_replacement,
        replacement_line_count=replacement_line_count,
        language=detect_language(file_path),
    )


def _normalise_category(raw: str, agent_name: str) -> str:
    """Return a canonical category string safe for cli._CATEGORY_MAP lookup.

    If *raw* (what the AI returned) is already a known canonical value, use it.
    Otherwise fall back to the agent's own canonical from _AGENT_CANONICAL_CATEGORY,
    defaulting to 'code-quality' for unknown agents.
    """
    normalised = raw.lower().strip()
    if normalised in _KNOWN_CATEGORIES:
        return normalised
    return _AGENT_CANONICAL_CATEGORY.get(agent_name, "code-quality")


# Sentinel used to detect already-prepended guard-rails (idempotency).
# Matches the first heading of guard_rails.md.
_GUARD_RAILS_SENTINEL = "# Guard Rails for Reviewer Agents"

# Matches the per-agent block delimited by HTML comments. Uses `.*?` with re.DOTALL
# so bullets containing literal '>' characters do not truncate the match.
_AGENT_BLOCK_PATTERN = re.compile(
    r"<!--\s*ANTI-PATTERNS.*?-->",
    re.DOTALL,
)
# Anchored to line start so subheaders (### foo) and inline `##` do not match.
_NEXT_TOP_SECTION_PATTERN = re.compile(r"^## ", re.MULTILINE)


def _prepend_guard_rails(system_prompt: str) -> str:
    """Prepend guard-rails (REVUE-244) to a reviewer agent's system prompt.

    Reads shared guard_rails.md, extracts the per-agent anti-pattern block from
    the prompt, substitutes it into the guard-rails Anti-patterns section, and
    prepends the merged result to the prompt.

    Returns original prompt unchanged if guard-rails file is missing or any
    fatal error occurs (logged at WARNING). Idempotent: re-running on a prompt
    that already contains guard-rails returns the prompt unchanged.
    """
    # Idempotency check — never double-prepend.
    if _GUARD_RAILS_SENTINEL in system_prompt:
        return system_prompt

    if not _SHARED_GUARD_RAILS_PATH.exists():
        Log.agent.warning(
            "[revue]     guard_rails.md missing at %s — reviewer prompts will not include guard rails",
            _SHARED_GUARD_RAILS_PATH,
        )
        return system_prompt

    try:
        guard_rails_text = _SHARED_GUARD_RAILS_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        Log.agent.warning(
            "[revue]     failed to read guard_rails.md (%s): %s",
            type(exc).__name__, exc,
        )
        return system_prompt

    agent_block_match = _AGENT_BLOCK_PATTERN.search(system_prompt)
    if not agent_block_match:
        return guard_rails_text + "\n\n" + system_prompt

    agent_block = agent_block_match.group(0)
    # Splice the block out of the prompt so it is not rendered to the LLM verbatim.
    prompt_without_block = (
        system_prompt[:agent_block_match.start()] +
        system_prompt[agent_block_match.end():]
    )

    # Strip HTML-comment markers and the ANTI-PATTERNS-<TAG> label line.
    block_content = agent_block.strip()
    if block_content.startswith("<!--"):
        block_content = block_content[4:]
    if block_content.endswith("-->"):
        block_content = block_content[:-3]
    block_content = block_content.strip()
    block_content = re.sub(r"^\s*ANTI-PATTERNS[-_A-Z]*\s*\n", "", block_content)

    # Insert the per-agent bullets at the end of the Anti-patterns section,
    # before the next top-level heading. If Anti-patterns is the last section,
    # append the bullets at the end of guard_rails_text. If the section is
    # missing entirely (guard_rails.md was edited away), log a warning and
    # append the bullets so per-agent guidance is not silently dropped.
    merged_guard_rails = guard_rails_text
    anti_pattern_idx = merged_guard_rails.find("## Anti-patterns")
    if anti_pattern_idx == -1:
        Log.agent.warning(
            "[revue]     guard_rails.md missing '## Anti-patterns' section; "
            "appending per-agent block at end of guard-rails"
        )
        merged_guard_rails = merged_guard_rails + "\n\n## Anti-patterns\n\n" + block_content
    else:
        search_start = anti_pattern_idx + len("## Anti-patterns")
        next_match = _NEXT_TOP_SECTION_PATTERN.search(merged_guard_rails, search_start)
        insert_at = next_match.start() if next_match else len(merged_guard_rails)
        merged_guard_rails = (
            merged_guard_rails[:insert_at] +
            "\n" + block_content + "\n\n" +
            merged_guard_rails[insert_at:]
        )

    return merged_guard_rails + "\n\n" + prompt_without_block


# ---------------------------------------------------------------------------
# Loaded agent — wraps definition + AI client (implements AgentProtocol)
# ---------------------------------------------------------------------------

class LoadedAgent:
    """
    A runnable agent loaded from a definition file.
    Implements AgentProtocol from agent_runner.py.
    Depends on AIClient Protocol (DIP).
    Optionally accepts ReadFileTool for lazy full-file reads (REVUE-241).
    """

    def __init__(
        self,
        definition: AgentDefinition,
        client: "AIClient",
        max_tokens: int,
        read_file_tool: "ReadFileTool | None" = None,
        read_lines_tool: "Any | None" = None,
        find_code_tool: "Any | None" = None,
    ) -> None:
        self._def = definition
        self._client = client
        self._max_tokens = max_tokens
        self._read_file_tool = read_file_tool
        # REVUE-243: complementary targeted-retrieval tools. Either / both can
        # be None; when None the agent simply doesn't see that tool.
        self._read_lines_tool = read_lines_tool
        self._find_code_tool = find_code_tool

    @property
    def name(self) -> str:
        return self._def.name

    @property
    def definition(self) -> AgentDefinition:
        return self._def

    def _invoke_client(
        self,
        user_content: str,
        system_blocks: list[dict],
        diff_hash: str,
    ) -> "CompletionResult":
        """Dispatch to ``complete_with_tools`` when the agent owns a tool and
        the client supports it; otherwise fall back to ``complete``.

        REVUE-241: encapsulates the with-tools / without-tools branch so
        ``analyse()`` keeps its single responsibility (prompt → parsed findings).

        REVUE-246: returns the full :class:`CompletionResult` (not just text)
        so ``analyse()`` can read ``stop_reason`` and ``iterations_used`` for
        terminal-state classification. The grammar-constraint is now the
        three-state schema, not the legacy bare-findings schema.
        """
        # REVUE-243: aggregate every wired tool. The agent sees whichever
        # tools were provided to __init__; absent tools are simply not
        # advertised. read_file remains for whole-file reads; read_lines and
        # find_code are the targeted-retrieval additions.
        wired_tools: list[Any] = [
            t for t in (self._read_file_tool, self._read_lines_tool, self._find_code_tool)
            if t is not None
        ]
        if wired_tools and hasattr(self._client, "complete_with_tools"):
            from .finding_schema import output_config_for_three_state
            call_site = f"{type(self._client).__name__}.complete_with_tools"
            tool_definitions = [t.tool_definition() for t in wired_tools]
            tool_handlers = {
                t.tool_definition()["name"]: t.execute for t in wired_tools
            }
            try:
                return self._client.complete_with_tools(
                    [{"role": "user", "content": user_content}],
                    system=system_blocks,
                    tools=tool_definitions,
                    tool_handlers=tool_handlers,
                    max_iterations=self._def.max_tool_iterations,
                    agent_name=self._def.name,
                    max_tokens=self._max_tokens,
                    output_config=output_config_for_three_state(),
                    reasoning_enabled=True,  # REVUE-337: opt into reasoning channel for reviewer agents
                )
            except Exception as exc:
                exc.call_site = call_site  # type: ignore[attr-defined]
                raise
        call_site = f"{type(self._client).__name__}.complete"
        try:
            return self._client.complete(
                [{"role": "user", "content": user_content}],
                system=system_blocks,
                cache_key=diff_hash,
                agent_name=self._def.name,
                max_tokens=self._max_tokens,
            )
        except Exception as exc:
            exc.call_site = call_site  # type: ignore[attr-defined]
            raise

    def analyse(
        self,
        changes: list[FileChange],
        shared: "SharedAnalysisResult | None" = None,
    ) -> "AgentVerdict":
        """
        Run this agent's analysis on the provided changes.

        Returns an :class:`AgentVerdict` with one of three statuses:

        * ``findings`` — the model produced one or more findings.
        * ``clean``    — the model reviewed the diff and found nothing.
        * ``error``    — the model could not produce a verdict (empty response,
          schema mismatch, refusal, iteration cap exhaustion, or self-declared
          tool unavailability after fallback). The legacy ``{"findings": []}``
          shape — what reviewers emitted pre-REVUE-246 — is now classified as
          ``error("invalid_response_schema")`` because AC8 forbids a shim.

        Network / auth / HTTP errors propagate; agent_runner converts them
        into a ``status: error`` AgentRunResult one level up.
        """
        import hashlib

        from .terminal_state import classify_terminal_state

        diff_text = _build_diff_text(changes)
        shared_context = _build_shared_context(shared) if shared else ""
        # Stable 16-char routing key for this diff — passed as prompt_cache_key
        # to OpenAI-compatible clients so re-reviews of the same PR land on the
        # same cache server and hit the cached prefix. Anthropic ignores it.
        diff_hash = hashlib.sha256(diff_text.encode()).hexdigest()[:16]

        # D1: diff is system[0] with cache_control (shared cached prefix across agents)
        # agent instructions are system[1] uncached (agent-specific)
        system_blocks = [
            {"type": "text", "text": diff_text, "cache_control": _CACHE_CONTROL_1H},
            {"type": "text", "text": f"The code diff above is what you must review. {self._def.system_prompt}"},
        ]
        user_content = (
            f"{shared_context}"
            "Carefully review the code diff for bugs, security issues, performance "
            "problems, and code quality concerns. "
            f"{_REVIEW_INSTRUCTIONS}"
        )
        result = self._invoke_client(user_content, system_blocks, diff_hash)
        raw = result.text
        Log.agent.info(
            "[revue]     [%s] raw response (%d chars, starts: %r)",
            self._def.name, len(raw), raw[:80],
        )

        terminal = classify_terminal_state(
            raw_text=raw,
            stop_reason=getattr(result, "stop_reason", None),
            iterations_used=getattr(result, "iterations_used", 1),
            max_iterations=self._def.max_tool_iterations,
            hit_iteration_cap=getattr(result, "hit_iteration_cap", False),
        )

        if terminal.state == "findings":
            verdict = self._build_findings_verdict(terminal.payload)
            Log.agent.info(
                "[revue]     [%s] verdict=findings count=%d",
                self._def.name, len(verdict.findings),
            )
            return verdict

        if terminal.state == "clean":
            payload = terminal.payload
            Log.agent.info(
                "[revue]     [%s] verdict=clean confidence=%s summary=%r",
                self._def.name, payload.get("confidence"),
                (payload.get("summary") or "")[:80],
            )
            return AgentVerdict.from_clean(
                summary=payload["summary"], confidence=float(payload["confidence"]),
            )

        # Error path. Surface the code so operators can triage refusal vs
        # schema mismatch vs iteration exhaustion, etc.
        err = terminal.payload["error"]
        Log.agent.warning(
            "[revue]     [%s] verdict=error code=%s message=%s",
            self._def.name, err["code"], err["message"],
        )
        Log.agent.debug(
            "[revue]     [%s] full raw response (%d chars):\n%s",
            self._def.name, len(raw), raw,
        )
        return AgentVerdict.from_error(
            code=err["code"],
            message=err["message"],
            iterations_used=err.get("iterations_used"),
        )

    def _build_findings_verdict(self, payload: dict[str, Any]) -> "AgentVerdict":
        """Parse the validated findings payload into an AgentVerdict.

        Each finding goes through ``_parse_finding_item`` so legacy severity
        synonyms and category normalisation continue to work — the three-state
        schema enforces the envelope; per-item normalisation is unchanged.
        """
        reviews: list[AIReview] = []
        for idx, item in enumerate(payload.get("findings", [])):
            raw_line = item.get("line") or item.get("lines") or item.get("line_number")
            raw_file = item.get("file_path") or item.get("filename") or item.get("file") or "(no file field)"
            raw_issue = (item.get("issue") or item.get("message") or item.get("title") or "")[:80]
            Log.position.info(
                "[pos] agent.finding[%d]  agent=%s  raw_line=%r  raw_file=%r  issue=%r  "
                "has_code_replacement=%s  raw_rlc=%r",
                idx, self._def.name, raw_line, raw_file, raw_issue,
                bool(item.get("code_replacement")),
                item.get("replacement_line_count"),
            )
            parsed = _parse_finding_item(item, self._def.name, self._def.severity_default)
            if parsed is not None:
                reviews.append(parsed)
        summary = payload.get("summary") if isinstance(payload.get("summary"), str) else None
        return AgentVerdict.from_findings(reviews, summary=summary)


# ---------------------------------------------------------------------------
# Parser Protocol (OCP — new formats implement this)
# ---------------------------------------------------------------------------

class AgentDefinitionParser(Protocol):
    """Protocol for agent definition file parsers."""
    def can_parse(self, path: Path) -> bool: ...
    def parse(self, path: Path) -> AgentDefinition: ...


# ---------------------------------------------------------------------------
# YAML parser
# ---------------------------------------------------------------------------

class YAMLAgentParser:
    """Parse agent definitions from .yaml / .yml files."""

    def can_parse(self, path: Path) -> bool:
        return path.suffix in {".yaml", ".yml"}

    def parse(self, path: Path) -> AgentDefinition:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        definition = _dict_to_definition(data, source=str(path))
        # REVUE-244: prepend guard_rails to reviewer system_prompts.
        if definition.name in _AGENT_CANONICAL_CATEGORY:
            definition.system_prompt = _prepend_guard_rails(definition.system_prompt)
        return definition


# ---------------------------------------------------------------------------
# Markdown front-matter parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


class MarkdownAgentParser:
    """Parse agent definitions from .md files with YAML front-matter."""

    def can_parse(self, path: Path) -> bool:
        return path.suffix == ".md"

    def parse(self, path: Path) -> AgentDefinition:
        text = path.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(text)
        if not match:
            raise ValueError(f"No YAML front-matter found in {path}")
        front_matter = yaml.safe_load(match.group(1)) or {}
        body = match.group(2).strip()
        # Body becomes system_prompt if not specified in front-matter
        if "system_prompt" not in front_matter and body:
            front_matter["system_prompt"] = body
        definition = _dict_to_definition(front_matter, source=str(path))
        # REVUE-244: prepend guard_rails to reviewer system_prompts only.
        # Non-reviewer agents (e.g. cleo, nova, vex) keep their original prompts.
        if definition.name in _AGENT_CANONICAL_CATEGORY:
            definition.system_prompt = _prepend_guard_rails(definition.system_prompt)
        return definition


# ---------------------------------------------------------------------------
# Agent loader
# ---------------------------------------------------------------------------

_DEFAULT_PARSERS: list[AgentDefinitionParser] = [
    YAMLAgentParser(),
    MarkdownAgentParser(),
]


def load_agent_definition(path: str | Path) -> AgentDefinition:
    """
    Load a single agent definition from a YAML or Markdown file.
    Raises ValueError if no parser can handle the file.
    """
    p = Path(path)
    for parser in _DEFAULT_PARSERS:
        if parser.can_parse(p):
            return parser.parse(p)
    raise ValueError(f"No parser for agent definition file: {path}")


def load_agents_from_dir(
    directory: str | Path,
    client: "AIClient",
    max_tokens: int,
    parsers: list[AgentDefinitionParser] | None = None,
    read_file_tool: "ReadFileTool | None" = None,
    read_lines_tool: "Any | None" = None,
    find_code_tool: "Any | None" = None,
) -> list[LoadedAgent]:
    """
    Load all agent definitions from a directory.

    - Scans for .yaml, .yml, .md files
    - Skips disabled agents
    - Returns list of LoadedAgent instances ready to run
    - Optionally threads read_file_tool / read_lines_tool / find_code_tool
      for the reviewer agent tool-use surface (REVUE-241 / REVUE-243)
    """
    active_parsers = parsers or _DEFAULT_PARSERS
    dir_path = Path(directory)
    agents: list[LoadedAgent] = []

    for file_path in sorted(dir_path.iterdir()):
        for parser in active_parsers:
            if parser.can_parse(file_path):
                try:
                    definition = parser.parse(file_path)
                    if definition.enabled:
                        agents.append(
                            LoadedAgent(
                                definition, client, max_tokens,
                                read_file_tool=read_file_tool,
                                read_lines_tool=read_lines_tool,
                                find_code_tool=find_code_tool,
                            )
                        )
                except Exception:
                    pass  # skip unparseable files silently
                break

    return agents


# ---------------------------------------------------------------------------
# Custom agent loading (Story [030])
# ---------------------------------------------------------------------------

def _is_safe_path(file_path: Path, base_dir: Path) -> bool:
    """Return True if *file_path* resolves inside *base_dir* (no symlink escape)."""
    try:
        resolved = file_path.resolve(strict=True)
    except OSError:
        return False
    return resolved == base_dir or str(resolved).startswith(str(base_dir) + "/")


def load_custom_agents(
    custom_agents_dir: str,
    parsers: list[AgentDefinitionParser] | None = None,
) -> list[AgentDefinition]:
    """
    Load project-specific agent definitions from *custom_agents_dir*.

    - If *custom_agents_dir* is empty or None → return []
    - If directory does not exist → log warning, return []
    - Scan for *.yaml, *.yml, *.md files
    - Parse each using the standard parsers
    - Skip files that fail validation (log warning, continue)
    - Reject paths that resolve outside *custom_agents_dir* (symlink escape)
    - Return list of AgentDefinition objects
    """
    if not custom_agents_dir:
        return []

    dir_path = Path(custom_agents_dir)
    if not dir_path.is_dir():
        Log.agent.warning("Custom agents directory does not exist: %s", custom_agents_dir)
        return []

    resolved_base = dir_path.resolve(strict=True)
    active_parsers = parsers or _DEFAULT_PARSERS
    definitions: list[AgentDefinition] = []

    for file_path in sorted(dir_path.iterdir()):
        if not _is_safe_path(file_path, resolved_base):
            Log.agent.warning("Skipping path outside custom agents dir: %s", file_path)
            continue
        for parser in active_parsers:
            if parser.can_parse(file_path):
                try:
                    definition = parser.parse(file_path)
                    definitions.append(definition)
                except Exception as exc:
                    Log.agent.warning("Skipping invalid custom agent %s: %s", file_path, exc)
                break

    return definitions


def load_all_agents(
    config: "AIConfig",
    client: "AIClient",
    builtin_agents_dir: str | None = None,
    read_file_tool: "ReadFileTool | None" = None,
    read_lines_tool: "Any | None" = None,
    find_code_tool: "Any | None" = None,
) -> list[LoadedAgent]:
    """
    Load built-in agents + custom agents, with custom overriding built-ins by name.

    1. Load built-in agents from *builtin_agents_dir* (or default ``agents/`` dir).
    2. Load custom agents from ``config.custom_agents_dir``.
    3. Custom agents with the same name as a built-in replace the built-in (logged at INFO).
    4. Disabled agents (``enabled: false``) are excluded.
    5. Optionally threads the three reviewer tools (REVUE-241 / REVUE-243).
    """
    if builtin_agents_dir is None:
        builtin_agents_dir = str(Path(__file__).resolve().parent.parent / "agents")

    max_tokens = config.ai_max_tokens
    builtin = load_agents_from_dir(
        builtin_agents_dir, client, max_tokens,
        read_file_tool=read_file_tool,
        read_lines_tool=read_lines_tool,
        find_code_tool=find_code_tool,
    )
    agents_by_name: dict[str, LoadedAgent] = {a.name: a for a in builtin}

    custom_defs = load_custom_agents(config.custom_agents_dir)
    for defn in custom_defs:
        if not defn.enabled:
            continue
        if defn.name in agents_by_name:
            Log.agent.info("Custom agent '%s' overrides built-in agent", defn.name)
        agents_by_name[defn.name] = LoadedAgent(
            defn, client, max_tokens,
            read_file_tool=read_file_tool,
            read_lines_tool=read_lines_tool,
            find_code_tool=find_code_tool,
        )

    return list(agents_by_name.values())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dict_to_definition(data: dict, source: str = "") -> AgentDefinition:
    name = data.get("name", "")
    if not name:
        raise ValueError(f"Agent definition missing required 'name' field in {source}")
    return AgentDefinition(
        name=name,
        display_name=data.get("display_name", name.title()),
        role=data.get("role", ""),
        system_prompt=data.get("system_prompt", ""),
        focus_areas=list(data.get("focus_areas", [])),
        trigger_patterns=list(data.get("trigger_patterns", [])),
        expertise=str(data.get("expertise", "")),
        severity_default=data.get("severity_default", "minor"),
        enabled=bool(data.get("enabled", True)),
        version=str(data.get("version", "1.0")),
        max_tool_iterations=int(data.get("max_tool_iterations", 5)),
    )


def _build_diff_text(changes: list[FileChange]) -> str:
    return "\n\n".join(
        f"File: {fc.file_path}\n{fc.diff}" for fc in changes
    )


def _build_shared_context(shared: "SharedAnalysisResult") -> str:
    return (
        f"Context from shared analysis:\n"
        f"Languages: {', '.join(shared.languages)}\n"
        f"Risk areas: {', '.join(shared.risk_areas)}\n"
        f"Summary: {shared.summary}\n\n"
    )
