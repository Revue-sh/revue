"""REVUE-377 (Option B): the distribution doc must describe the install-skill
trust model that the code actually implements — no more, no less.

`cmd_install_skill` (packaging/revue/src/revue_skill/cli.py) performs, in the
default path: https-validated manifest fetch → JSON-schema validation →
advertised-version vs installed ``__version__`` match. It does NOT compute a
wheel sha256 and does NOT verify a Sigstore/cosign signature, and the tag
pipeline does not sign release artefacts. The doc previously claimed all three.

These tests are regression guards for AC1/AC4/AC5: they fail if the doc
re-introduces a claim of a control the code does not perform, or lists a test
file that does not exist.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGING_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PACKAGING_DIR.parent.parent
DOC = REPO_ROOT / "docs" / "distribution" / "revue-skill-packaging.md"


def _doc_text() -> str:
    assert DOC.is_file(), f"distribution doc missing at {DOC}"
    return DOC.read_text(encoding="utf-8")


def test_doc_test_tree_references_only_real_tests():
    """Every ``test_*.py`` named anywhere in the doc must resolve to a real
    file under some ``packaging/*/tests/`` directory. Catches phantom entries
    like ``test_release_artefact_signed.py`` /
    ``test_signature_verification_in_installer.py`` (lines 80/82) that imply a
    signing/verification test which does not exist.
    """
    real_tests = {
        p.name for p in REPO_ROOT.glob("packaging/*/tests/**/test_*.py")
    }
    named = set(re.findall(r"test_[a-z0-9_]+\.py", _doc_text()))

    phantom = sorted(name for name in named if name not in real_tests)
    assert not phantom, (
        "doc references test files that do not exist under packaging/*/tests/: "
        f"{phantom}. Either the test must exist or the reference must be removed."
    )


def test_doc_does_not_claim_unimplemented_install_verification():
    """Regression guard for the specific false claims (AC1/AC5).

    Tolerant of a clearly-labelled *planned / not yet implemented* mention of
    signing that points at the Option A follow-up — only a claim of a control
    as something the code *currently does* fails the test.
    """
    text = _doc_text()
    lowered = text.lower()

    # 1) The install path is claimed to verify wheel hash + Sigstore signature.
    assert "wheel hash + sigstore signature" not in lowered, (
        "doc claims install-skill verifies wheel hash + Sigstore signature; "
        "the install path does neither (cli.py cmd_install_skill)."
    )
    # Match a verb + hash/signature token + "before copying" within a single
    # sentence ([^.]* never crosses a period). Tolerates the accurate
    # "...without --skip-verify) applies three checks before copying..." prose
    # (no hash/signature token) and the negated "does not yet verify ...
    # signature" sentence (no "before copying").
    assert not re.search(
        r"verif\w+\b[^.]*\b(hash|signature|sigstore|cosign)\b[^.]*\bbefore copying",
        lowered,
    ), (
        "doc claims the install path verifies a wheel hash/signature 'before "
        "copying' the skill; the install path performs neither."
    )

    # 2) The release set is claimed to be cryptographically 'signed'.
    assert not re.search(r"built,\s*signed,\s*and\s+published", lowered), (
        "doc claims the release set is 'signed'; the tag pipeline performs no "
        "signing (no cosign/Sigstore/gpg step in bitbucket-pipelines.yml)."
    )

    # 3) Generalised guard (closes the "before copying"-less rephrasing hole):
    #    any sentence that pairs a verify-stem with a hash/signature token must
    #    also be negated or explicitly flagged as planned. This passes the
    #    accurate "does not yet verify ... sha256 ... nor a Sigstore signature
    #    ... planned ... (REVUE-378)" prose and the true "pip already verifies
    #    the PyPI-provided hash" statement, but fails a present-tense claim such
    #    as "the install path verifies the wheel sha256".
    prose = re.sub(r"`[^`]*`", " ", lowered)  # drop inline-code spans (dotted tokens)
    hash_sig = ("sha256", "wheel hash", "sigstore", "cosign", "signature")
    negators = ("not", "planned", "advisory", "without", "pip already", "no ")
    for sentence in re.split(r"(?<=[.!?])\s+|\n", prose):
        if re.search(r"\bverif\w+", sentence) and any(t in sentence for t in hash_sig):
            assert any(n in sentence for n in negators), (
                "doc sentence asserts hash/signature verification as a current "
                f"control without negation/planned qualifier: {sentence.strip()!r}"
            )
