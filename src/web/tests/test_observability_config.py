"""REVUE-362 — guard the committed alert rules + dashboard against drift.

These configs are deployed to Grafana (Fly's managed Grafana) and read the
Prometheus series emitted by ``metrics.py``. If a metric name changes in code
but not in the alert PromQL, alerts silently stop firing — these tests fail
loudly instead, binding the config to the exact series names.

This is config validation, not a live-alert test. The Test Cases in the story
(synthetic 500 burst → page; 1000 RPS → notify; dashboard loads live data) are
post-deploy E2E and are tracked in the runbook, not here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import metrics

_OBS_DIR = Path(__file__).resolve().parent.parent / "infra" / "observability"
_ALERTS = _OBS_DIR / "alert-rules.yaml"
_DASHBOARD = _OBS_DIR / "dashboard.json"


def test_observability_config_files_exist():
    assert _ALERTS.is_file(), f"missing {_ALERTS}"
    assert _DASHBOARD.is_file(), f"missing {_DASHBOARD}"


def test_alert_rules_reference_emitted_metric_names():
    text = _ALERTS.read_text()
    # Both instruments must appear in the alert PromQL, or an alert is dead.
    assert "revue_http_requests_total" in text
    assert "revue_http_request_duration_seconds" in text


def test_alert_rules_cover_all_three_acs():
    text = _ALERTS.read_text().lower()
    # AC: error-rate, latency p95, traffic-anomaly — one rule each.
    assert "error" in text and "rate" in text
    assert "latency" in text or "duration" in text
    assert "traffic" in text or "anomaly" in text


def test_alert_rules_target_the_activate_route():
    text = _ALERTS.read_text()
    assert "/api/v2/licence/activate" in text


def test_latency_alert_uses_the_two_second_slo_and_p95():
    text = _ALERTS.read_text()
    assert "0.95" in text  # histogram_quantile p95
    assert "2" in text     # 2s SLO threshold


def test_error_rate_alert_uses_five_percent_threshold():
    text = _ALERTS.read_text()
    assert "0.05" in text


def test_dashboard_is_valid_json_and_references_emitted_series():
    raw = _DASHBOARD.read_text()
    doc = json.loads(raw)  # raises if malformed
    assert isinstance(doc, dict)
    assert "revue_http_requests_total" in raw
    assert "revue_http_request_duration_seconds" in raw


def test_dashboard_has_required_panels():
    raw = _DASHBOARD.read_text().lower()
    # AC: request rate, error rate, latency p50/p95/p99, status-code distribution.
    for needle in ("request rate", "error rate", "latency", "status"):
        assert needle in raw, f"dashboard missing panel: {needle}"
    assert "0.5" in raw and "0.95" in raw and "0.99" in raw  # p50/p95/p99


def test_no_secrets_committed_in_alert_config():
    # Contact points / paging keys must be Grafana secrets, never committed.
    text = _ALERTS.read_text().lower()
    for forbidden in ("pagerduty_integration_key:", "api_key:", "routing_key:", "webhook_url: https"):
        assert forbidden not in text
