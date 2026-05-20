"""Test that all revue_core modules can be imported."""

import pytest


def test_import_core_models():
    """Test importing from revue_core.core.models."""
    from revue_core.core.models import Severity, FileChange, AIReview, PRContext
    assert Severity.CRITICAL


def test_import_core_log():
    """Test importing from revue_core.core.log."""
    from revue_core.core.log import RevueLogger
    assert RevueLogger


def test_import_core_logging_channels():
    """Test importing from revue_core.core.logging_channels."""
    from revue_core.core.logging_channels import Log
    assert Log


def test_import_core_diff_parser():
    """Test importing from revue_core.core.diff_parser."""
    from revue_core.core.diff_parser import parse_diff_file
    assert callable(parse_diff_file)


def test_import_comments_models():
    """Test importing from revue_core.comments.models."""
    from revue_core.comments.models import AgentFinding
    assert AgentFinding


def test_import_comments_consolidator():
    """Test importing from revue_core.comments.consolidator."""
    from revue_core.comments.consolidator import Consolidator
    assert Consolidator


def test_import_comments_verifier():
    """Test importing from revue_core.comments._verifier."""
    from revue_core.comments._verifier import VexVerifier
    assert VexVerifier


def test_import_core_agent_loader():
    """Test importing from revue_core.core.agent_loader."""
    from revue_core.core.agent_loader import load_all_agents
    assert callable(load_all_agents)


def test_import_core_pipeline():
    """Test importing from revue_core.core.pipeline."""
    from revue_core.core.pipeline import ReviewPipeline
    assert ReviewPipeline


# The leaf-package constraint lives in test_leaf_constraint.py.
