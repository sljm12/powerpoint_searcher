ALTER TABLE search.service_state ADD COLUMN IF NOT EXISTS reindex_id UUID;
ALTER TABLE search.service_state ADD COLUMN IF NOT EXISTS target_profile TEXT;
ALTER TABLE search.decks ADD COLUMN IF NOT EXISTS excluded_revision BIGINT;
CREATE TABLE IF NOT EXISTS search.reindexes (
    id UUID PRIMARY KEY,
    library_id UUID NOT NULL,
    profile_id TEXT NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE
);
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM search.schema_version WHERE id=1 AND version=2) THEN
        ALTER TABLE search.profiles ADD COLUMN dimensions INTEGER
            GENERATED ALWAYS AS ((configuration->>'dimensions')::integer) STORED NOT NULL;
        ALTER TABLE search.profiles ADD UNIQUE (profile_id, dimensions);
        ALTER TABLE search.segments ALTER COLUMN embedding TYPE VECTOR;
        ALTER TABLE search.segments ADD COLUMN dimensions INTEGER
            GENERATED ALWAYS AS (vector_dims(embedding)) STORED NOT NULL;
        ALTER TABLE search.segments ADD FOREIGN KEY (profile_id, dimensions)
            REFERENCES search.profiles (profile_id, dimensions);
        UPDATE search.schema_version SET version=3 WHERE id=1;
    END IF;
END $$;
