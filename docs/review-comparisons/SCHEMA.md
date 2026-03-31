# Review Knowledge Base — Postgres Schema
**Version:** 1  
**DB host:** 192.168.0.36:5432 (NAS — QNAP Container Station)  
**Tracked by:** REVUE-88 (infra), REVUE-89 (schema)

---

## Design Principles

- **3NF throughout**: anything that can change independently gets its own table
- **Schema changes = INSERT rows**, not ALTER TABLE (lookup tables absorb new values)
- **NAS unreachable = graceful degrade** — scripts warn and continue, never crash
- **raw_json preserved** on findings as audit trail; normalised columns are queryable projections
- **Human ratings take precedence** over auto-heuristic scores always

---

## Schema

```sql
-- ─────────────────────────────────────────────────────────────
-- Versioning
-- ─────────────────────────────────────────────────────────────

CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,  -- ISO-8601
    description TEXT NOT NULL
);
INSERT INTO schema_version VALUES (1, NOW()::text, 'Initial schema');


-- ─────────────────────────────────────────────────────────────
-- Lookup / reference tables
-- Add new values with INSERT — no ALTER TABLE needed
-- ─────────────────────────────────────────────────────────────

CREATE TABLE severity_levels (
    id            SERIAL PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,  -- 'critical','high','medium','low','info'
    display_order INTEGER NOT NULL
);

CREATE TABLE review_modes (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL  -- 'baseline', 'contextual'
);

CREATE TABLE models (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL  -- 'claude-sonnet-4-5', 'gpt-4o', ...
);

CREATE TABLE tiers (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL  -- 'free', 'indie', 'pro'
);

CREATE TABLE agents (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL  -- 'orchestrator', 'security-expert', 'code-quality-expert', ...
);

CREATE TABLE categories (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL  -- 'security', 'code-quality', 'testing', 'architecture', ...
);

CREATE TABLE fp_reasons (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
    -- 'intentional_design', 'out_of_scope', 'pattern_known',
    -- 'covered_elsewhere', 'other'
);

CREATE TABLE pattern_types (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL  -- 'allowed', 'disallowed'
);

CREATE TABLE quality_dimensions (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,  -- 'clarity', 'actionability'
    description TEXT NOT NULL
);

CREATE TABLE rating_sources (
    id   SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL  -- 'human', 'auto'
);


-- ─────────────────────────────────────────────────────────────
-- Core entities
-- ─────────────────────────────────────────────────────────────

CREATE TABLE reviews (
    id              SERIAL PRIMARY KEY,
    ticket          TEXT NOT NULL,        -- 'REVUE-86'
    branch          TEXT NOT NULL,
    diff_size_lines INTEGER NOT NULL,
    model_id        INTEGER NOT NULL REFERENCES models(id),
    tier_id         INTEGER NOT NULL REFERENCES tiers(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Raw PR description — one row per review
CREATE TABLE pr_descriptions (
    id        SERIAL PRIMARY KEY,
    review_id INTEGER NOT NULL UNIQUE REFERENCES reviews(id) ON DELETE CASCADE,
    raw_text  TEXT NOT NULL
);

-- Normalised sections — add new section types with INSERT, not ALTER TABLE
CREATE TABLE pr_description_sections (
    id                  SERIAL PRIMARY KEY,
    pr_description_id   INTEGER NOT NULL REFERENCES pr_descriptions(id) ON DELETE CASCADE,
    section_name        TEXT NOT NULL,  -- 'summary', 'out_of_scope', 'changes', 'dependencies', ...
    content             TEXT NOT NULL,
    UNIQUE (pr_description_id, section_name)
);


-- ─────────────────────────────────────────────────────────────
-- Findings
-- ─────────────────────────────────────────────────────────────

CREATE TABLE findings (
    id          SERIAL PRIMARY KEY,
    review_id   INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    mode_id     INTEGER NOT NULL REFERENCES review_modes(id),
    agent_id    INTEGER REFERENCES agents(id),      -- NULL for free-tier (no agent routing)
    file_path   TEXT NOT NULL,
    severity_id INTEGER NOT NULL REFERENCES severity_levels(id),
    category_id INTEGER REFERENCES categories(id),
    issue       TEXT NOT NULL,
    line_number INTEGER,
    -- raw_json: full AI response for this finding, preserved as audit trail.
    -- Normalised columns above are queryable projections of this data.
    -- Use raw_json when you need fields not yet promoted to columns.
    raw_json    JSONB NOT NULL
);

-- Quality scores: separate table so adding dimensions = INSERT into quality_dimensions
CREATE TABLE finding_quality (
    id               SERIAL PRIMARY KEY,
    finding_id       INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    dimension_id     INTEGER NOT NULL REFERENCES quality_dimensions(id),
    score            INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    notes            TEXT,
    rating_source_id INTEGER NOT NULL REFERENCES rating_sources(id),
    rated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (finding_id, dimension_id, rating_source_id)
    -- Human and auto scores can both exist; queries should prefer human
);

-- Outcomes: separate so actioning logic evolves independently
CREATE TABLE finding_outcomes (
    id                INTEGER PRIMARY KEY REFERENCES findings(id) ON DELETE CASCADE,
    was_actioned      BOOLEAN NOT NULL DEFAULT FALSE,
    is_false_positive BOOLEAN NOT NULL DEFAULT FALSE,
    fp_reason_id      INTEGER REFERENCES fp_reasons(id),
    actioned_at       TIMESTAMPTZ,
    notes             TEXT
);


-- ─────────────────────────────────────────────────────────────
-- Comparisons (A/B-safe via junction)
-- ─────────────────────────────────────────────────────────────

CREATE TABLE comparisons (
    id         SERIAL PRIMARY KEY,
    ticket     TEXT NOT NULL,  -- denormalised for fast lookup
    notes      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Junction: supports multiple runs per comparison (A/B description testing)
CREATE TABLE comparison_runs (
    id            SERIAL PRIMARY KEY,
    comparison_id INTEGER NOT NULL REFERENCES comparisons(id) ON DELETE CASCADE,
    review_id     INTEGER NOT NULL REFERENCES reviews(id),
    role          TEXT NOT NULL CHECK (role IN ('baseline', 'contextual')),
    UNIQUE (comparison_id, review_id)
);

-- Derived metrics: computed at import time, updated on re-run
CREATE TABLE comparison_metrics (
    id                    SERIAL PRIMARY KEY,
    comparison_id         INTEGER NOT NULL UNIQUE REFERENCES comparisons(id) ON DELETE CASCADE,
    suppressed_count      INTEGER NOT NULL DEFAULT 0,  -- in baseline, not contextual
    preserved_count       INTEGER NOT NULL DEFAULT 0,  -- in both
    new_in_contextual     INTEGER NOT NULL DEFAULT 0,  -- only in contextual
    computed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ─────────────────────────────────────────────────────────────
-- Patterns
-- ─────────────────────────────────────────────────────────────

CREATE TABLE patterns (
    id             SERIAL PRIMARY KEY,
    type_id        INTEGER NOT NULL REFERENCES pattern_types(id),
    pattern        TEXT NOT NULL,   -- regex or substring — e.g. '_def\.'
    description    TEXT NOT NULL,   -- human explanation of why this is allowed/disallowed
    source_ticket  TEXT,            -- 'REVUE-83' — where we first observed the need
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Junction: which patterns matched which findings
-- matched_by and matched_at distinguish auto-detection from human retroactive labelling
CREATE TABLE finding_pattern_matches (
    finding_id       INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    pattern_id       INTEGER NOT NULL REFERENCES patterns(id) ON DELETE CASCADE,
    matched_by       TEXT NOT NULL CHECK (matched_by IN ('auto', 'human')),
    matched_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (finding_id, pattern_id)
);
```

---

## Key Queries

```sql
-- Which false positive patterns recur most?
SELECT p.pattern, p.description, COUNT(*) AS hits
FROM finding_pattern_matches fpm
JOIN patterns p ON p.id = fpm.pattern_id
JOIN finding_outcomes fo ON fo.finding_id = fpm.finding_id
WHERE fo.is_false_positive = TRUE
GROUP BY p.id ORDER BY hits DESC;

-- Which findings are consistently unclear? (human clarity < 3)
SELECT f.issue, f.file_path, AVG(fq.score) AS avg_clarity
FROM finding_quality fq
JOIN quality_dimensions qd ON qd.id = fq.dimension_id AND qd.name = 'clarity'
JOIN rating_sources rs ON rs.id = fq.rating_source_id AND rs.name = 'human'
JOIN findings f ON f.id = fq.finding_id
GROUP BY f.id HAVING AVG(fq.score) < 3 ORDER BY avg_clarity;

-- Context suppression rate over time
SELECT r.ticket, cm.suppressed_count, cm.preserved_count,
       ROUND(cm.suppressed_count * 100.0 /
             NULLIF(cm.suppressed_count + cm.preserved_count, 0), 1) AS suppression_pct
FROM comparison_metrics cm
JOIN comparisons c ON c.id = cm.comparison_id
JOIN comparison_runs cr ON cr.comparison_id = c.id AND cr.role = 'baseline'
JOIN reviews r ON r.id = cr.review_id
ORDER BY r.created_at;

-- Per-agent false positive rate
SELECT a.name AS agent, COUNT(*) FILTER (WHERE fo.is_false_positive) AS fp_count,
       COUNT(*) AS total,
       ROUND(COUNT(*) FILTER (WHERE fo.is_false_positive) * 100.0 / COUNT(*), 1) AS fp_pct
FROM findings f
JOIN agents a ON a.id = f.agent_id
LEFT JOIN finding_outcomes fo ON fo.id = f.id
GROUP BY a.id ORDER BY fp_pct DESC;
```

---

## Seed Data

```sql
INSERT INTO severity_levels(name, display_order) VALUES
    ('critical',1),('high',2),('medium',3),('low',4),('info',5);

INSERT INTO review_modes(name) VALUES ('baseline'), ('contextual');

INSERT INTO models(name) VALUES
    ('claude-sonnet-4-5'), ('claude-sonnet-4-6'), ('gpt-4o'), ('gpt-4o-mini');

INSERT INTO tiers(name) VALUES ('free'), ('indie'), ('pro');

INSERT INTO agents(name) VALUES
    ('orchestrator'),('code-quality-expert'),('security-expert'),
    ('performance-expert'),('architecture-expert'),('documentation-expert'),
    ('consolidator');

INSERT INTO quality_dimensions(name, description) VALUES
    ('clarity',      'Was the finding clearly explained? Could a mid-level dev understand it without googling?'),
    ('actionability','Was there a concrete fix? Or vague advice?');

INSERT INTO rating_sources(name) VALUES ('human'), ('auto');

INSERT INTO pattern_types(name) VALUES ('allowed'), ('disallowed');

INSERT INTO fp_reasons(name) VALUES
    ('intentional_design'),('out_of_scope'),
    ('pattern_known'),('covered_elsewhere'),('other');
```
