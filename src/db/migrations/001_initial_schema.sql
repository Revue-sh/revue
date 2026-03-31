-- Migration 001: Initial Review Knowledge Base Schema
-- Created: 2026-03-31
-- Story: REVUE-89
-- Description: Normalised Postgres schema for storing review comparisons,
--              false positives, quality ratings, and PR context tracking.

-- =============================================================================
-- SCHEMA VERSION TRACKING (Gap 3)
-- =============================================================================

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY CHECK (version >= 1),
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT NOT NULL
);

-- Enforce single-row constraint
CREATE UNIQUE INDEX IF NOT EXISTS idx_schema_version_singleton ON schema_version ((1));

-- Insert initial version
INSERT INTO schema_version (version, description)
VALUES (1, 'Initial schema: reviews, findings, quality ratings, patterns')
ON CONFLICT (version) DO NOTHING;

-- =============================================================================
-- REFERENCE TABLES (Lookups / Enums)
-- =============================================================================

-- Severity levels for findings
CREATE TABLE IF NOT EXISTS severity_levels (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) UNIQUE NOT NULL CHECK (name IN ('critical', 'high', 'medium', 'low', 'info'))
);

-- Review modes (baseline vs contextual)
CREATE TABLE IF NOT EXISTS review_modes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) UNIQUE NOT NULL CHECK (name IN ('baseline', 'contextual'))
);

-- AI models used for reviews
CREATE TABLE IF NOT EXISTS models (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    provider VARCHAR(50) NOT NULL CHECK (provider IN ('anthropic', 'openai')),
    context_window INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- License tiers
CREATE TABLE IF NOT EXISTS tiers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) UNIQUE NOT NULL CHECK (name IN ('free', 'pro', 'enterprise'))
);

-- Quality dimensions for rating findings
CREATE TABLE IF NOT EXISTS quality_dimensions (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL CHECK (name IN ('clarity', 'actionability'))
);

-- False positive reasons
CREATE TABLE IF NOT EXISTS fp_reasons (
    id SERIAL PRIMARY KEY,
    code VARCHAR(50) UNIQUE NOT NULL,
    description TEXT NOT NULL
);

-- Pattern types (allowed vs disallowed)
CREATE TABLE IF NOT EXISTS pattern_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) UNIQUE NOT NULL CHECK (name IN ('allowed', 'disallowed'))
);

-- Rating sources (human vs auto) (Gap 2)
CREATE TABLE IF NOT EXISTS rating_sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) UNIQUE NOT NULL CHECK (name IN ('human', 'auto'))
);

-- AI agents catalog (Gap 1)
CREATE TABLE IF NOT EXISTS agents (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    version VARCHAR(50),
    role TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================================
-- CORE TABLES
-- =============================================================================

-- Reviews: Each review run (baseline or contextual)
CREATE TABLE IF NOT EXISTS reviews (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(50) NOT NULL,  -- e.g. REVUE-89
    branch VARCHAR(255) NOT NULL,
    model_id INTEGER NOT NULL REFERENCES models(id),
    tier_id INTEGER NOT NULL REFERENCES tiers(id),
    mode_id INTEGER NOT NULL REFERENCES review_modes(id),
    run_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_seconds INTEGER,
    total_findings INTEGER DEFAULT 0,
    -- raw_json JSONB,  -- Gap 6: Redundant with findings table, but useful for debugging/auditing
    UNIQUE (ticket_id, branch, mode_id, run_at)
);

CREATE INDEX IF NOT EXISTS idx_reviews_ticket ON reviews(ticket_id);
CREATE INDEX IF NOT EXISTS idx_reviews_mode ON reviews(mode_id);
CREATE INDEX IF NOT EXISTS idx_reviews_run_at ON reviews(run_at DESC);

-- Findings: Individual AI findings from a review
CREATE TABLE IF NOT EXISTS findings (
    id SERIAL PRIMARY KEY,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES agents(id),  -- Gap 1
    file_path TEXT NOT NULL,
    line_start INTEGER,
    line_end INTEGER,
    severity_id INTEGER NOT NULL REFERENCES severity_levels(id),
    category VARCHAR(100),
    issue TEXT NOT NULL,
    details TEXT,
    recommendation TEXT,
    code_snippet TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_findings_review ON findings(review_id);
CREATE INDEX IF NOT EXISTS idx_findings_agent ON findings(agent_id);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity_id);
CREATE INDEX IF NOT EXISTS idx_findings_file ON findings(file_path);

-- Finding Quality: Human and auto ratings (Gap 2)
CREATE TABLE IF NOT EXISTS finding_quality (
    id SERIAL PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    dimension_id INTEGER NOT NULL REFERENCES quality_dimensions(id),
    score INTEGER NOT NULL CHECK (score >= 1 AND score <= 5),
    rated_by_id INTEGER NOT NULL REFERENCES rating_sources(id),  -- Gap 2: FK to rating_sources
    rated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (finding_id, dimension_id, rated_by_id)  -- One rating per dimension per source
);

CREATE INDEX IF NOT EXISTS idx_finding_quality_finding ON finding_quality(finding_id);
CREATE INDEX IF NOT EXISTS idx_finding_quality_rated_by ON finding_quality(rated_by_id);

-- Finding Outcomes: False positive tracking
CREATE TABLE IF NOT EXISTS finding_outcomes (
    id SERIAL PRIMARY KEY,
    finding_id INTEGER NOT NULL UNIQUE REFERENCES findings(id) ON DELETE CASCADE,
    is_false_positive BOOLEAN NOT NULL DEFAULT FALSE,
    fp_reason_id INTEGER REFERENCES fp_reasons(id),
    notes TEXT,
    assessed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (is_false_positive = FALSE OR fp_reason_id IS NOT NULL)  -- FP requires reason
);

CREATE INDEX IF NOT EXISTS idx_finding_outcomes_is_fp ON finding_outcomes(is_false_positive);

-- PR Descriptions: Contextual information used in reviews
CREATE TABLE IF NOT EXISTS pr_descriptions (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(50) NOT NULL,
    description_text TEXT NOT NULL,
    sha256_hash CHAR(64) UNIQUE NOT NULL,  -- Deduplication
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pr_descriptions_ticket ON pr_descriptions(ticket_id);
CREATE INDEX IF NOT EXISTS idx_pr_descriptions_hash ON pr_descriptions(sha256_hash);

-- PR Description Sections: Parsed sections (summary, out of scope, etc.)
CREATE TABLE IF NOT EXISTS pr_description_sections (
    id SERIAL PRIMARY KEY,
    pr_description_id INTEGER NOT NULL REFERENCES pr_descriptions(id) ON DELETE CASCADE,
    section_type VARCHAR(50) NOT NULL CHECK (section_type IN ('summary', 'background', 'out_of_scope', 'dependencies', 'notes')),
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pr_sections_pr ON pr_description_sections(pr_description_id);

-- Comparison Runs: Baseline vs Contextual pairs (Gap 4)
CREATE TABLE IF NOT EXISTS comparison_runs (
    id SERIAL PRIMARY KEY,
    ticket_id VARCHAR(50) NOT NULL,
    baseline_review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    contextual_review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    pr_description_id INTEGER REFERENCES pr_descriptions(id),
    suppression_rate FLOAT,  -- % of findings suppressed by context
    run_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (baseline_review_id, contextual_review_id),
    CHECK (baseline_review_id != contextual_review_id)
);

CREATE INDEX IF NOT EXISTS idx_comparison_runs_ticket ON comparison_runs(ticket_id);
CREATE INDEX IF NOT EXISTS idx_comparison_runs_baseline ON comparison_runs(baseline_review_id);
CREATE INDEX IF NOT EXISTS idx_comparison_runs_contextual ON comparison_runs(contextual_review_id);

-- Allowed Patterns: .revue.yml noise filters
CREATE TABLE IF NOT EXISTS allowed_patterns (
    id SERIAL PRIMARY KEY,
    pattern_text TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (pattern_text)
);

-- Disallowed Patterns: .revue.yml noise filters
CREATE TABLE IF NOT EXISTS disallowed_patterns (
    id SERIAL PRIMARY KEY,
    pattern_text TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (pattern_text)
);

-- Finding Pattern Matches: Tracking which findings matched which patterns (Gap 5)
CREATE TABLE IF NOT EXISTS finding_pattern_matches (
    id SERIAL PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    pattern_type_id INTEGER NOT NULL REFERENCES pattern_types(id),
    pattern_id INTEGER NOT NULL,  -- FK to allowed_patterns or disallowed_patterns (polymorphic)
    matched_by VARCHAR(50) NOT NULL CHECK (matched_by IN ('system_prompt', 'post_processing')),  -- Gap 5
    matched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- Gap 5
    UNIQUE (finding_id, pattern_type_id, pattern_id)
);

CREATE INDEX IF NOT EXISTS idx_pattern_matches_finding ON finding_pattern_matches(finding_id);
CREATE INDEX IF NOT EXISTS idx_pattern_matches_pattern_type ON finding_pattern_matches(pattern_type_id);

-- =============================================================================
-- SEED DATA
-- =============================================================================

-- Severity levels
INSERT INTO severity_levels (name) VALUES
    ('critical'),
    ('high'),
    ('medium'),
    ('low'),
    ('info')
ON CONFLICT (name) DO NOTHING;

-- Review modes
INSERT INTO review_modes (name) VALUES
    ('baseline'),
    ('contextual')
ON CONFLICT (name) DO NOTHING;

-- Models
INSERT INTO models (name, provider, context_window) VALUES
    ('claude-sonnet-4-5', 'anthropic', 200000),
    ('claude-haiku-4-5', 'anthropic', 200000),
    ('claude-3-5-sonnet-20241022', 'anthropic', 200000),
    ('gpt-4-turbo', 'openai', 128000),
    ('gpt-4o', 'openai', 128000)
ON CONFLICT (name) DO NOTHING;

-- Tiers
INSERT INTO tiers (name) VALUES
    ('free'),
    ('pro'),
    ('enterprise')
ON CONFLICT (name) DO NOTHING;

-- Quality dimensions
INSERT INTO quality_dimensions (name) VALUES
    ('clarity'),
    ('actionability')
ON CONFLICT (name) DO NOTHING;

-- False positive reasons
INSERT INTO fp_reasons (code, description) VALUES
    ('intentional_pattern', 'Intentional design decision documented in .revue.yml'),
    ('test_code', 'Test file or test-specific pattern not applicable to production'),
    ('out_of_scope', 'Finding addresses something explicitly marked out of scope in PR description'),
    ('duplicate_coverage', 'Coverage exists elsewhere (e.g. test_vcs_adapters.py covers test_vcs_adapter.py)'),
    ('framework_requirement', 'Required by framework or library constraints'),
    ('legacy_code', 'Legacy code not being refactored in this PR'),
    ('other', 'Other reason (see notes field)')
ON CONFLICT (code) DO NOTHING;

-- Pattern types
INSERT INTO pattern_types (name) VALUES
    ('allowed'),
    ('disallowed')
ON CONFLICT (name) DO NOTHING;

-- Rating sources (Gap 2)
INSERT INTO rating_sources (name) VALUES
    ('human'),
    ('auto')
ON CONFLICT (name) DO NOTHING;

-- Agents (Gap 1)
INSERT INTO agents (name, version, role) VALUES
    ('orchestrator', '1.0', 'Coordinates review workflow and delegates to specialist agents'),
    ('code-quality', '1.0', 'Reviews code quality, naming, style, and maintainability'),
    ('architecture', '1.0', 'Reviews architectural decisions, patterns, and system design'),
    ('security', '1.0', 'Reviews security vulnerabilities and best practices'),
    ('performance', '1.0', 'Reviews performance issues and optimization opportunities'),
    ('testing', '1.0', 'Reviews test coverage, quality, and patterns'),
    ('documentation', '1.0', 'Reviews documentation completeness and clarity'),
    ('consolidator', '1.0', 'Merges and deduplicates findings from specialist agents')
ON CONFLICT (name) DO NOTHING;

-- =============================================================================
-- VALIDATION QUERIES (Run after migration to verify)
-- =============================================================================

-- Check schema version
-- SELECT * FROM schema_version;

-- Check reference tables populated
-- SELECT 'severity_levels' as table_name, COUNT(*) as row_count FROM severity_levels
-- UNION ALL SELECT 'review_modes', COUNT(*) FROM review_modes
-- UNION ALL SELECT 'models', COUNT(*) FROM models
-- UNION ALL SELECT 'tiers', COUNT(*) FROM tiers
-- UNION ALL SELECT 'quality_dimensions', COUNT(*) FROM quality_dimensions
-- UNION ALL SELECT 'fp_reasons', COUNT(*) FROM fp_reasons
-- UNION ALL SELECT 'pattern_types', COUNT(*) FROM pattern_types
-- UNION ALL SELECT 'rating_sources', COUNT(*) FROM rating_sources
-- UNION ALL SELECT 'agents', COUNT(*) FROM agents;

-- List all tables
-- \dt

-- Check constraints on key tables
-- \d findings
-- \d finding_quality
-- \d comparison_runs
