"""Pytest config for the scripts/ unit tests.

Puts the repo ``scripts/`` directory on ``sys.path`` so the test modules can
``import staging_e2e_accounts`` / ``import provision_staging_e2e`` directly,
mirroring how ``python3 scripts/<x>.py`` runs them (with ``scripts/`` as
``sys.path[0]``). Without this, a ``spec_from_file_location`` load of the
provisioner would fail to resolve its ``from staging_e2e_accounts import ...``
sibling import.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
