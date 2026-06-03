"""Shared configuration — avoids circular imports between main and routes."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

# Single source of truth for the pricing currency symbol. Change here and every
# surface follows: templates via the Jinja global below, Python via import.
CURRENCY_SYMBOL = "$"

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["currency_symbol"] = CURRENCY_SYMBOL
