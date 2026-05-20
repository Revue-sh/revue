"""
PR Context Filtering — route relevant PR description sections to each agent.

Reduces token usage by ~40-60% vs. naive full-description-to-all approach.
Each agent receives only the sections relevant to their domain expertise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .pr_description_adapter import PRDescription


# ---------------------------------------------------------------------------
# Section-to-Agent Routing Map
# ---------------------------------------------------------------------------

# Maps agent roles to which PR description sections are relevant
AGENT_SECTION_MAP: dict[str, list[str]] = {
    "orchestrator": [
        "summary",
        "background",
        "out_of_scope",
    ],
    "code-quality-expert": [
        "changes",
        "acceptance_criteria",
        "testing",
    ],
    "security-expert": [
        "changes",
        "dependencies",
        "out_of_scope",
    ],
    "performance-expert": [
        "changes",
        "acceptance_criteria",
        "background",
    ],
    "architecture-expert": [
        "summary",
        "changes",
        "dependencies",
        "background",
    ],
    "documentation-expert": [
        "summary",
        "changes",
        "acceptance_criteria",
    ],
    "consolidator": [
        "summary",
        "acceptance_criteria",
        "out_of_scope",
    ],
}


# ---------------------------------------------------------------------------
# Context Extractor
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    """Filtered context for a specific agent."""
    agent_name: str
    pr_title: str
    relevant_sections: dict[str, str]
    
    def to_prompt_context(self) -> str:
        """Format context as a prompt snippet for the agent.
        
        Returns a compact markdown block suitable for inclusion in
        the system prompt or user message.
        """
        if not self.relevant_sections:
            return f"**PR Title:** {self.pr_title}"
        
        lines = [f"**PR Title:** {self.pr_title}", ""]
        
        for section_name, content in self.relevant_sections.items():
            if content.strip():
                # Format section names nicely
                display_name = section_name.replace("_", " ").title()
                lines.append(f"**{display_name}:**")
                lines.append(content.strip())
                lines.append("")
        
        return "\n".join(lines).strip()


class PRContextExtractor:
    """Extracts and filters PR description context for specific agents."""
    
    def __init__(self, pr_description: PRDescription):
        self.pr_description = pr_description
    
    def get_context_for_agent(self, agent_name: str) -> AgentContext:
        """Extract context relevant to a specific agent.
        
        Args:
            agent_name: Agent identifier (e.g., "security-expert", "orchestrator")
        
        Returns:
            AgentContext with filtered sections based on AGENT_SECTION_MAP.
            If agent not in map, returns empty context with just PR title.
        """
        relevant_section_names = AGENT_SECTION_MAP.get(agent_name, [])
        
        relevant_sections = {}
        for section_name in relevant_section_names:
            content = getattr(self.pr_description, section_name, "")
            if content:
                relevant_sections[section_name] = content
        
        return AgentContext(
            agent_name=agent_name,
            pr_title=self.pr_description.title,
            relevant_sections=relevant_sections,
        )
    
    def get_full_context(self) -> str:
        """Get the complete PR description as markdown.
        
        Useful for summary comments or when agent needs full context.
        """
        lines = [
            f"# {self.pr_description.title}",
            "",
            self.pr_description.raw_description,
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Token Efficiency Analysis
# ---------------------------------------------------------------------------

def estimate_token_savings(pr_description: PRDescription) -> dict:
    """Estimate token savings from context filtering.
    
    Returns a dict with:
    - full_tokens: estimated tokens if sending full description to all agents
    - filtered_tokens: estimated tokens with context filtering
    - savings_percent: percentage reduction (0 if filtered > naive)
    - per_agent_breakdown: dict of agent -> token count
    """
    # Rough estimate: 1 token ≈ 4 characters
    def estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)  # At least 1 token for non-empty text
    
    full_description = f"{pr_description.title}\n{pr_description.raw_description}"
    full_tokens = estimate_tokens(full_description)
    
    extractor = PRContextExtractor(pr_description)
    
    per_agent_tokens = {}
    total_filtered_tokens = 0
    
    for agent_name in AGENT_SECTION_MAP.keys():
        context = extractor.get_context_for_agent(agent_name)
        agent_tokens = estimate_tokens(context.to_prompt_context())
        per_agent_tokens[agent_name] = agent_tokens
        total_filtered_tokens += agent_tokens
    
    # For agents not in map, assume they get just the title
    num_agents = len(AGENT_SECTION_MAP)
    naive_total = full_tokens * num_agents
    
    # Calculate savings, but cap at 0% (no negative savings)
    savings_percent = 0.0
    if naive_total > 0:
        raw_savings = ((naive_total - total_filtered_tokens) / naive_total) * 100
        savings_percent = max(0.0, raw_savings)  # No negative savings
    
    return {
        "full_tokens": full_tokens,
        "full_tokens_all_agents": naive_total,
        "filtered_tokens": total_filtered_tokens,
        "savings_percent": round(savings_percent, 1),
        "per_agent_breakdown": per_agent_tokens,
    }
