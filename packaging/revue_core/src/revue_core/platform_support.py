"""Canonical supported-platform policy (REVUE-360).

SINGLE SOURCE OF TRUTH for which platforms Revue publishes per-OS Nuitka
wheels for. Every other consumer derives from this module:

* ``scripts/install.sh``        — the curl|bash installer's pre-pip guard
* ``docs/guides/install.md``    — the install page's supported-platform list
* ``revue_ci.cli`` / ``revue_skill.cli`` — ``version`` output platform line

``tests/test_supported_platforms_consistency.py`` fails CI if the installer
or the install page drifts from this list, so the three surfaces can never
hold copy-pasted lists that disagree.

This is a *policy* list (which platforms we ship wheels for). It is distinct
from the build-time wheel *tag* logic in ``packaging/*/build/build_wheel.py``,
which enumerates more platforms because it is about naming a wheel file, not
about what we actually publish.
"""
from __future__ import annotations

from dataclasses import dataclass

#: The install page. Pre-MVP this resolves to the GitHub copy of the guide;
#: post-MVP it will redirect from ``https://revue.sh/install``. Kept here so
#: the installer and docs reference one URL, never a hand-copied literal.
INSTALL_PAGE_URL = "https://github.com/cbscd/revue/blob/main/docs/guides/install.md"

#: The fallback offered to users on unsupported platforms: run Revue in CI.
CI_WORKAROUND = (
    "run Revue in your CI pipeline via the revue-ci integration "
    "(github/gitlab/bitbucket) instead"
)

#: Single arch-alias normalisation rule, mirrored verbatim in
#: ``scripts/install.sh``. Keep both sides in sync (the consistency test does
#: not parse this map, so changing it here means changing the shell ``case``).
_MACHINE_ALIASES = {"amd64": "x86_64"}


@dataclass(frozen=True)
class SupportedPlatform:
    """A platform Revue publishes a wheel for.

    ``system`` and ``machine`` are canonical lowercase ``uname -s`` / ``uname -m``
    tokens (machine after :func:`normalise_machine`). ``label`` is the
    human-readable name shown in guard messages and the install page.
    """

    system: str
    machine: str
    label: str


#: The published wheel matrix (Phase 2.a): macOS ARM64 + Linux x86_64 only.
SUPPORTED_PLATFORMS: tuple[SupportedPlatform, ...] = (
    SupportedPlatform(system="darwin", machine="arm64", label="macOS ARM64"),
    SupportedPlatform(system="linux", machine="x86_64", label="Linux x86_64"),
)


def normalise_machine(machine: str) -> str:
    """Lowercase a ``uname -m`` token and fold known arch aliases."""
    token = machine.strip().lower()
    return _MACHINE_ALIASES.get(token, token)


def _match(system: str, machine: str) -> SupportedPlatform | None:
    """Return the matching :class:`SupportedPlatform`, or None if unsupported.

    Single matching routine shared by :func:`is_supported` and
    :func:`format_platform_status_line` so the predicate and the label lookup
    can never disagree.
    """
    sys_token = system.strip().lower()
    machine_token = normalise_machine(machine)
    for p in SUPPORTED_PLATFORMS:
        if p.system == sys_token and p.machine == machine_token:
            return p
    return None


def is_supported(system: str, machine: str) -> bool:
    """Return True iff ``(system, machine)`` is a published wheel target."""
    return _match(system, machine) is not None


def supported_labels() -> tuple[str, ...]:
    """Human-readable labels for every supported platform, in registry order."""
    return tuple(p.label for p in SUPPORTED_PLATFORMS)


def unsupported_message(system: str, machine: str) -> str:
    """Build the AC1 guard message for an unsupported platform.

    Names the platform, links the install page, and states the CI workaround.
    """
    labels = ", ".join(supported_labels())
    return (
        f"Revue does not publish a wheel for your platform: {system} {machine}. "
        f"Supported platforms: {labels}. "
        f"See {INSTALL_PAGE_URL} for the supported-platform list. "
        f"Workaround: {CI_WORKAROUND}."
    )


def format_platform_status_line(system: str, machine: str) -> str:
    """One-line platform summary for CLI ``version`` output.

    On a supported platform, names it; otherwise flags it UNSUPPORTED and
    links the install page (so a source/editable dev install on an unsupported
    box is surfaced rather than silently mis-run).
    """
    matched = _match(system, machine)
    if matched is not None:
        return f"Platform: {matched.label} (supported)"
    return f"Platform: {system} {machine} (UNSUPPORTED — see {INSTALL_PAGE_URL})"
