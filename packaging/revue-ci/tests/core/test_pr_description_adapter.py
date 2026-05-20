"""Tests for pr_description_adapter.py — PR description fetching."""
import pytest
from unittest.mock import Mock, patch
from revue_core.core.pr_description_adapter import (
    PRDescription,
    get_bitbucket_pr_description,
    get_github_pr_description,
    get_gitlab_pr_description,
    detect_platform_from_env,
    get_pr_description_from_env,
)


# ---------------------------------------------------------------------------
# PRDescription.parse() tests
# ---------------------------------------------------------------------------

def test_pr_description_parse_empty():
    """Empty description returns empty sections."""
    pr = PRDescription.parse("Test PR", "")
    assert pr.title == "Test PR"
    assert pr.raw_description == ""
    assert pr.summary == ""
    assert pr.out_of_scope == ""


def test_pr_description_parse_summary():
    """Parses ## Summary section."""
    body = """
## Summary
This PR adds a new feature.

Some more details here.
"""
    pr = PRDescription.parse("Feature PR", body)
    assert pr.summary.startswith("This PR adds")
    assert "Some more details" in pr.summary


def test_pr_description_parse_multiple_sections():
    """Parses multiple standard sections."""
    body = """
## Summary
Adds authentication.

## Changes
- Add login endpoint
- Add JWT middleware

## Out of Scope
- Password reset (deferred to REVUE-99)

## Dependencies
- Requires database migration
"""
    pr = PRDescription.parse("Auth PR", body)
    assert "authentication" in pr.summary.lower()
    assert "login endpoint" in pr.changes.lower()
    assert "REVUE-99" in pr.out_of_scope
    assert "migration" in pr.dependencies.lower()


def test_pr_description_parse_case_insensitive():
    """Section detection is case-insensitive."""
    body = """
## SUMMARY
Case test

## out of scope
Not included
"""
    pr = PRDescription.parse("Test", body)
    assert "Case test" in pr.summary
    assert "Not included" in pr.out_of_scope


def test_pr_description_parse_alternate_markers():
    """Recognizes alternate section names."""
    body = """
## Background
Historical context here.

## What Changed
Code changes.

## Test Plan
Testing details.
"""
    pr = PRDescription.parse("Test", body)
    assert "Historical" in pr.background
    assert "Code changes" in pr.changes
    assert "Testing details" in pr.testing


# ---------------------------------------------------------------------------
# Bitbucket fetcher tests
# ---------------------------------------------------------------------------

@patch("httpx.get")
def test_get_bitbucket_pr_description_success(mock_get):
    """Fetches PR description from Bitbucket API."""
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = {
        "title": "Test PR",
        "description": "## Summary\nTest description",
    }
    mock_get.return_value = mock_response
    
    pr = get_bitbucket_pr_description("ws", "repo", 123, "user", "token")
    
    assert pr is not None
    assert pr.title == "Test PR"
    assert "Test description" in pr.summary


@patch("httpx.get")
def test_get_bitbucket_pr_description_error(mock_get):
    """Returns None on API error."""
    mock_get.side_effect = Exception("API error")
    
    pr = get_bitbucket_pr_description("ws", "repo", 123, "user", "token")
    
    assert pr is None


# ---------------------------------------------------------------------------
# GitHub fetcher tests
# ---------------------------------------------------------------------------

@patch("httpx.get")
def test_get_github_pr_description_success(mock_get):
    """Fetches PR from GitHub API."""
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = {
        "title": "GitHub PR",
        "body": "## Changes\nSome changes",
    }
    mock_get.return_value = mock_response
    
    pr = get_github_pr_description("owner", "repo", 456, "token")
    
    assert pr is not None
    assert pr.title == "GitHub PR"
    assert "Some changes" in pr.changes


@patch("httpx.get")
def test_get_github_pr_description_error(mock_get):
    """Returns None on API error."""
    mock_get.side_effect = Exception("API error")
    
    pr = get_github_pr_description("owner", "repo", 456, "token")
    
    assert pr is None


# ---------------------------------------------------------------------------
# GitLab fetcher tests
# ---------------------------------------------------------------------------

@patch("httpx.get")
def test_get_gitlab_pr_description_success(mock_get):
    """Fetches MR from GitLab API."""
    mock_response = Mock()
    mock_response.raise_for_status = Mock()
    mock_response.json.return_value = {
        "title": "GitLab MR",
        "description": "## Testing\nTest plan",
    }
    mock_get.return_value = mock_response
    
    pr = get_gitlab_pr_description("123", 789, "token")
    
    assert pr is not None
    assert pr.title == "GitLab MR"
    assert "Test plan" in pr.testing


@patch("httpx.get")
def test_get_gitlab_pr_description_error(mock_get):
    """Returns None on API error."""
    mock_get.side_effect = Exception("API error")
    
    pr = get_gitlab_pr_description("123", 789, "token")
    
    assert pr is None


# ---------------------------------------------------------------------------
# Auto-detection tests
# ---------------------------------------------------------------------------

def test_detect_platform_from_env_bitbucket(monkeypatch):
    """Detects Bitbucket from env vars."""
    monkeypatch.setenv("BITBUCKET_WORKSPACE", "test")
    assert detect_platform_from_env() == "bitbucket"


def test_detect_platform_from_env_github(monkeypatch):
    """Detects GitHub from env vars."""
    # BITBUCKET_WORKSPACE is always set in Bitbucket CI — clear it so GitHub takes priority
    monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert detect_platform_from_env() == "github"


def test_detect_platform_from_env_gitlab(monkeypatch):
    """Detects GitLab from env vars."""
    monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITLAB_CI", "true")
    assert detect_platform_from_env() == "gitlab"


def test_detect_platform_from_env_none(monkeypatch):
    """Returns None when no platform detected."""
    monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    assert detect_platform_from_env() is None


@patch("revue_core.core.pr_description_adapter.get_bitbucket_pr_description")
def test_get_pr_description_from_env_bitbucket(mock_get, monkeypatch):
    """Fetches from Bitbucket when env vars present."""
    monkeypatch.setenv("BITBUCKET_WORKSPACE", "ws")
    monkeypatch.setenv("BITBUCKET_REPO_SLUG", "repo")
    monkeypatch.setenv("BITBUCKET_USERNAME", "user")
    monkeypatch.setenv("BITBUCKET_API_TOKEN", "token")
    
    mock_pr = PRDescription("Test", "Body")
    mock_get.return_value = mock_pr
    
    pr = get_pr_description_from_env(123)
    
    assert pr == mock_pr
    mock_get.assert_called_once_with("ws", "repo", 123, "user", "token")


@patch("revue_core.core.pr_description_adapter.get_github_pr_description")
def test_get_pr_description_from_env_github(mock_get, monkeypatch):
    """Fetches from GitHub when env vars present."""
    monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    
    mock_pr = PRDescription("Test", "Body")
    mock_get.return_value = mock_pr
    
    pr = get_pr_description_from_env(456)
    
    assert pr == mock_pr
    mock_get.assert_called_once_with("owner", "repo", 456, "token")


@patch("revue_core.core.pr_description_adapter.get_gitlab_pr_description")
def test_get_pr_description_from_env_gitlab(mock_get, monkeypatch):
    """Fetches from GitLab when env vars present."""
    monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITLAB_CI", "true")
    monkeypatch.setenv("CI_PROJECT_ID", "123")
    monkeypatch.setenv("GITLAB_TOKEN", "token")
    
    mock_pr = PRDescription("Test", "Body")
    mock_get.return_value = mock_pr
    
    pr = get_pr_description_from_env(789)
    
    assert pr == mock_pr
    mock_get.assert_called_once_with("123", 789, "token", "https://gitlab.com")


def test_get_pr_description_from_env_missing_vars(monkeypatch):
    """Returns None when required vars missing."""
    monkeypatch.setenv("BITBUCKET_WORKSPACE", "ws")
    # Missing other required vars
    
    pr = get_pr_description_from_env(123)
    assert pr is None


def test_get_pr_description_from_env_no_platform(monkeypatch):
    """Returns None when no platform detected."""
    monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    
    pr = get_pr_description_from_env(123)
    assert pr is None
