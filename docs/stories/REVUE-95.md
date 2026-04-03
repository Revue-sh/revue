# REVUE-95: Orchestrator Agent Selection Transparency

## User Story
As a developer, I want to understand which code areas triggered which review agents and why, so I can trust the review process, anticipate feedback, and learn what aspects of my changes require extra scrutiny.

## Background
The orchestrator currently selects agents silently. Developers cannot see which code areas triggered which agents or understand the reasoning. This creates a black box experience that reduces trust and learning opportunities.

## Acceptance Criteria
1. **AC1:** Orchestrator returns structured JSON with `detected_areas` and `selected_agents` arrays
2. **AC2:** JSON response is validated using Pydantic models (`OrchestratorResponse`, `DetectedArea`, `SelectedAgent`)
3. **AC3:** `format_selection_message()` function formats JSON into human-readable output with emojis
4. **AC4:** Output follows consistent format: "🔍 Analyzing..." → detected areas → selected agents → "Starting review..."
5. **AC5:** Orchestrator supports structured JSON responses for OpenAI, Anthropic, Google, and Groq providers
6. **AC6:** Provider detection uses correct `response_format` parameter (OpenAI/Google/Groq: `{"type": "json_object"}`, Anthropic: prompt engineering or tool schema)
7. **AC7:** Rationale is high-level (e.g., "for auth review") — no agent internal logic exposed
8. **AC8:** Existing orchestrator tests pass (no regression)

## Test Cases
1. **TC1-Structured-JSON:** Orchestrator returns valid JSON with required fields → AC1
2. **TC2-Pydantic-Validation:** JSON is parsed and validated by Pydantic models → AC2
3. **TC3-Format-Message:** `format_selection_message()` produces correct output format → AC3, AC4
4. **TC4-Provider-OpenAI:** OpenAI provider uses `{"type": "json_object"}` format → AC5, AC6
5. **TC5-Provider-Anthropic:** Anthropic provider uses prompt engineering for JSON → AC5, AC6
6. **TC6-IP-Protection:** Output contains high-level rationale only → AC7
7. **TC7-Regression:** Existing orchestrator tests pass → AC8

## Out of Scope
- Exposing agent internal prompts or decision-making logic
- System-wide CI log formatting (separate story: REVUE-105)
- Agent naming/branding (keep generic: "Security Agent" not "Sentinel")

## Dependencies
None

## Technical Notes

### JSON Schema
```json
{
  "detected_areas": [
    {"emoji": "🔐", "description": "Authentication middleware (login flow updated)"},
    {"emoji": "🗄️", "description": "Database migrations (users table schema change)"}
  ],
  "selected_agents": [
    {"emoji": "🛡️", "name": "Security Agent", "reason": "auth review"},
    {"emoji": "🗄️", "name": "Data Agent", "reason": "schema validation"}
  ]
}
```

### Provider-Specific Handling
```python
def get_orchestrator_response(provider: str, prompt: str):
    if provider in ["openai", "google", "groq"]:
        return llm.complete(
            prompt=prompt,
            response_format={"type": "json_object"}
        )
    elif provider == "anthropic":
        return llm.complete(
            prompt=f"{prompt}\n\nRespond ONLY with valid JSON matching this schema: {{...}}",
        )
```

### Pydantic Models
```python
from pydantic import BaseModel

class DetectedArea(BaseModel):
    emoji: str
    description: str

class SelectedAgent(BaseModel):
    emoji: str
    name: str
    reason: str

class OrchestratorResponse(BaseModel):
    detected_areas: list[DetectedArea]
    selected_agents: list[SelectedAgent]
```

### Format Function
```python
def format_selection_message(data: dict) -> str:
    msg = "🔍 Analyzing your changes...\n\n"
    msg += "I've detected modifications in:\n"
    for area in data["detected_areas"]:
        msg += f"  {area['emoji']} {area['description']}\n"
    msg += "\nTo ensure quality, I'm bringing in:\n"
    for agent in data["selected_agents"]:
        msg += f"  → {agent['emoji']} {agent['name']} for {agent['reason']}\n"
    msg += "\nStarting review...\n"
    return msg
```

## Example Output
```
🔍 Analyzing your changes...

I've detected modifications in:
  🔐 Authentication middleware (login flow updated)
  🗄️ Database migrations (users table schema change)
  ⚡ API endpoints (new rate limiting logic)

To ensure quality, I'm bringing in:
  → 🛡️ Security Agent for auth review
  → 🗄️ Data Agent for schema validation
  → ⚡ Performance Agent for API optimization

Starting review...
```

## Estimate
2-3 days

## Epic
REVUE-87: Developer Experience & Transparency
