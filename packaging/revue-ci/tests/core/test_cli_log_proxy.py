"""REVUE-241: cli.py wires a proxy hook so Log.nova channel reaches stdout.

Without this proxy, ``Log.nova.info("[vex-verdict] …")`` only writes to the
file logger at ``~/.config/revue/logs/`` — invisible during a normal dogfood
run. The proxy hook in cli.py routes every dispatched channel message to
stdout so operators can audit Vex's per-finding decisions in real time.

The tests deliberately avoid ``sys.modules.pop`` / re-import to prevent
breaking ``@patch("revue_ci.cli.…")`` decorators in sibling test modules — the
singleton ``RevueLogger`` retains the proxy hook across the whole test
session once cli is imported.
"""
from __future__ import annotations


def test_cli_registers_revue_logger_proxy_hook() -> None:
    """Importing revue.cli installs a non-None proxy on RevueLogger.shared()."""
    import revue_ci.cli  # noqa: F401 — import for side effect
    from revue_core.core.log import RevueLogger

    assert RevueLogger.shared()._proxy_hook is not None, (
        "revue.cli must register a proxy hook so Log.nova / Log.pipeline "
        "channel messages reach stdout, not just the file logger"
    )


def test_proxy_hook_dispatches_nova_info_to_stdout(capsys) -> None:
    """An info-level message on Log.nova produces stdout output once the
    cli-installed proxy is active. Black-box guarantee: emit through the
    channel, observe on stdout — what makes ``[vex-verdict]`` lines visible
    during a dogfood run."""
    import revue_ci.cli  # noqa: F401 — import for side effect
    from revue_core.core.logging_channels import Log

    capsys.readouterr()  # drain any earlier output
    Log.nova.info("[vex-verdict] reject_finding src/foo.py:10 — already done")

    captured = capsys.readouterr().out
    assert "[vex-verdict]" in captured, (
        "Log.nova.info must reach stdout via the cli-registered proxy hook"
    )
    assert "reject_finding" in captured
    assert "src/foo.py:10" in captured
