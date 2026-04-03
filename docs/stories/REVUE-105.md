# REVUE-105: System-Wide Emoji-Friendly CI Logging

## User Story
As a developer, I want all CI logs to use consistent emoji-friendly formatting, so I can instantly identify key events (starting review, agent selection, errors, completion) and reduce cognitive load when debugging failed reviews.

## Background
Current CI logs are plain text, making it difficult to quickly scan for important information. Emoji-friendly formatting improves readability and developer efficiency by providing instant visual cues for status, code areas, and agent activity.

## Acceptance Criteria
1. **AC1:** New `HumanizedLogger` class wraps existing logger with emoji + formatting logic
2. **AC2:** Emoji vocabulary uses Enum classes grouped by category (Status, CodeArea, Agent)
3. **AC3:** CLI commands use `HumanizedLogger` for all output
4. **AC4:** CI runner uses `HumanizedLogger` for pipeline steps
5. **AC5:** Orchestrator output (from REVUE-95) uses `HumanizedLogger`
6. **AC6:** Emoji vocabulary is documented with Enum categories in code comments or README
7. **AC7:** Existing tests pass (no functional changes, only presentation)

## Test Cases
1. **TC1-Logger-Info:** `HumanizedLogger.info()` includes emoji and formatted correctly → AC1
2. **TC2-Logger-Error:** `HumanizedLogger.error()` includes ❌ and formatted correctly → AC1
3. **TC3-Enum-Vocab:** Emoji vocabulary uses Status, CodeArea, Agent enums → AC2
4. **TC4-CLI-Output:** CLI command output uses emojis → AC3
5. **TC5-CI-Runner:** CI runner output uses emojis → AC4
6. **TC6-Orchestrator:** Orchestrator uses `HumanizedLogger` → AC5
7. **TC7-Documentation:** Emoji vocabulary documented in code/README → AC6
8. **TC8-Regression:** All existing tests pass → AC7

## Out of Scope
- Changing log levels or verbosity
- Adding new log messages (only formatting existing ones)
- Custom emoji themes or user preferences (fixed emoji set for MVP)

## Dependencies
- REVUE-95 completed (orchestrator message format finalized)

## Technical Notes

### Emoji Vocabulary (Enums)
```python
# src/revue/core/logging/emoji_vocab.py
from enum import Enum

class Status(Enum):
    """Status and progress indicators"""
    ANALYZING = "🔍"
    PROCESSING = "⚙️"
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    INFO = "ℹ️"

class CodeArea(Enum):
    """Code areas detected in changes"""
    AUTH = "🔐"
    DATABASE = "🗄️"
    PERFORMANCE = "⚡"
    UI = "🎨"
    TESTING = "🧪"
    DEPENDENCIES = "📦"
    CONFIG = "🔧"

class Agent(Enum):
    """Review agents"""
    SECURITY = "🛡️"
    DATA = "🗄️"
    PERFORMANCE = "⚡"
    UI = "🎨"
```

### HumanizedLogger Class
```python
# src/revue/core/logging/humanized_logger.py
import logging
from .emoji_vocab import Status

class HumanizedLogger:
    """Wraps standard logger with emoji-friendly formatting"""
    
    def __init__(self, logger: logging.Logger):
        self.logger = logger
    
    def info(self, message: str, emoji: str = Status.INFO.value):
        self.logger.info(f"{emoji} {message}")
    
    def success(self, message: str):
        self.logger.info(f"{Status.SUCCESS.value} {message}")
    
    def error(self, message: str):
        self.logger.error(f"{Status.ERROR.value} {message}")
    
    def warning(self, message: str):
        self.logger.warning(f"{Status.WARNING.value} {message}")
```

### Usage Example
```python
from revue.core.logging import HumanizedLogger, Status, CodeArea
import logging

logger = HumanizedLogger(logging.getLogger(__name__))

# In CLI
logger.info("Starting code review...", emoji=Status.ANALYZING.value)

# In CI runner
logger.success("All tests passed")
logger.error("Build failed")

# In orchestrator (with REVUE-95 integration)
logger.info(f"Detected {CodeArea.AUTH.value} changes")
```

## File Structure
```
src/revue/core/logging/
  __init__.py              # Export HumanizedLogger, enums
  emoji_vocab.py           # Enum definitions (Status, CodeArea, Agent)
  humanized_logger.py      # HumanizedLogger class

tests/logging/
  test_humanized_logger.py # Unit tests for logger
  test_emoji_vocab.py      # Enum validation tests
```

## Implementation Checklist
- [ ] Create `emoji_vocab.py` with Status, CodeArea, Agent enums
- [ ] Create `HumanizedLogger` class
- [ ] Update CLI to use `HumanizedLogger`
- [ ] Update CI runner to use `HumanizedLogger`
- [ ] Update orchestrator to use `HumanizedLogger` (post-REVUE-95)
- [ ] Document emoji vocabulary in README or code comments
- [ ] Write unit tests (TC1-TC8)
- [ ] Manual smoke test: run full CI pipeline

## Estimate
1-2 days

## Epic
REVUE-87: Developer Experience & Transparency
