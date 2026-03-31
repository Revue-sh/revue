-- Migration 002: Drop agents table and findings.agent_id FK
-- Created: 2026-03-31
-- Story: REVUE-90 (prep work)
-- Description: Remove redundant agents table. AI model tracking already
--              exists via reviews.model_id FK. findings.category captures
--              the review type (security, performance, etc.).
--
-- Rationale: 
--   - agents table conflated AI model (Claude, GPT) with review category
--   - findings.agent_id redundant: model already tracked at review level
--   - Simplifies REVUE-90 importer (no agent lookup needed)
--
-- Use case preserved:
--   - Model comparison: JOIN findings → reviews → models
--   - Category analysis: GROUP BY findings.category

-- =============================================================================
-- MIGRATION
-- =============================================================================

BEGIN;

-- Drop FK constraint first
ALTER TABLE findings DROP CONSTRAINT IF EXISTS findings_agent_id_fkey;

-- Drop column
ALTER TABLE findings DROP COLUMN IF EXISTS agent_id;

-- Drop table
DROP TABLE IF EXISTS agents;

-- Update schema version
UPDATE schema_version SET version = 2, description = 'Dropped agents table (redundant with reviews.model_id)';

COMMIT;
