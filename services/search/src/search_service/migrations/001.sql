CREATE SCHEMA IF NOT EXISTS search;

CREATE TABLE IF NOT EXISTS search.schema_version (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);
INSERT INTO search.schema_version (id, version) VALUES (1, 1)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS search.service_state (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    library_id UUID,
    active_profile TEXT,
    maintenance BOOLEAN NOT NULL DEFAULT FALSE,
    embedding_dimensions INTEGER NOT NULL DEFAULT 0
);
ALTER TABLE search.service_state ADD COLUMN IF NOT EXISTS library_id UUID;
INSERT INTO search.service_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS search.profiles (
    profile_id TEXT PRIMARY KEY,
    configuration JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS search.decks (
    library_id UUID NOT NULL,
    deck_id UUID NOT NULL,
    requested_revision BIGINT NOT NULL,
    active_revision BIGINT,
    active_generation UUID,
    profile_id TEXT,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    eligible_slides INTEGER NOT NULL DEFAULT 0,
    searchable_slides INTEGER NOT NULL DEFAULT 0,
    blank_slides INTEGER NOT NULL DEFAULT 0,
    hidden_slides INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (library_id, deck_id)
);
ALTER TABLE search.decks ADD COLUMN IF NOT EXISTS eligible_slides INTEGER NOT NULL DEFAULT 0;
ALTER TABLE search.decks ADD COLUMN IF NOT EXISTS searchable_slides INTEGER NOT NULL DEFAULT 0;
ALTER TABLE search.decks ADD COLUMN IF NOT EXISTS blank_slides INTEGER NOT NULL DEFAULT 0;
ALTER TABLE search.decks ADD COLUMN IF NOT EXISTS hidden_slides INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS search.jobs (
    id UUID PRIMARY KEY,
    library_id UUID NOT NULL,
    deck_id UUID NOT NULL,
    generation_id UUID NOT NULL,
    source_revision BIGINT NOT NULL,
    index_revision BIGINT NOT NULL,
    profile_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL,
    payload JSONB,
    state TEXT NOT NULL CHECK (state IN ('queued','processing','ready','failed','cancelled')),
    completed_segments INTEGER NOT NULL DEFAULT 0,
    total_segments INTEGER NOT NULL DEFAULT 0,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    error JSONB,
    retry_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
    eligible_slides INTEGER NOT NULL DEFAULT 0,
    searchable_slides INTEGER NOT NULL DEFAULT 0,
    blank_slides INTEGER NOT NULL DEFAULT 0,
    hidden_slides INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (library_id, deck_id, index_revision)
);
ALTER TABLE search.jobs ADD COLUMN IF NOT EXISTS eligible_slides INTEGER NOT NULL DEFAULT 0;
ALTER TABLE search.jobs ADD COLUMN IF NOT EXISTS searchable_slides INTEGER NOT NULL DEFAULT 0;
ALTER TABLE search.jobs ADD COLUMN IF NOT EXISTS blank_slides INTEGER NOT NULL DEFAULT 0;
ALTER TABLE search.jobs ADD COLUMN IF NOT EXISTS hidden_slides INTEGER NOT NULL DEFAULT 0;
UPDATE search.schema_version SET version=2 WHERE id=1 AND version=1;
CREATE INDEX IF NOT EXISTS jobs_queue ON search.jobs (updated_at) WHERE state IN ('queued','processing');

CREATE TABLE IF NOT EXISTS search.segments (
    library_id UUID NOT NULL,
    deck_id UUID NOT NULL,
    generation_id UUID NOT NULL,
    source_revision BIGINT NOT NULL,
    index_revision BIGINT NOT NULL,
    profile_id TEXT NOT NULL,
    slide_number INTEGER NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('native','notes')),
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED,
    embedding VECTOR(__VECTOR_DIMENSIONS__) NOT NULL,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (library_id, deck_id, index_revision, slide_number, channel, ordinal)
);
CREATE INDEX IF NOT EXISTS segments_lexical ON search.segments USING GIN (search_vector);
