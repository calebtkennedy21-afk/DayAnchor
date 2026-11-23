CREATE TABLE IF NOT EXISTS legacy_notification_suppressions (
    id BIGSERIAL PRIMARY KEY,
    legacy_key TEXT NOT NULL,
    legacy_recorded_at TIMESTAMPTZ,
    destination_type TEXT NOT NULL,
    destination_id TEXT,
    destination_verified_at TIMESTAMPTZ,
    source_type TEXT,
    source_key TEXT,
    provenance TEXT NOT NULL,
    migration_batch_id TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    resolution_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT legacy_notification_suppressions_key_check
        CHECK (length(legacy_key) BETWEEN 1 AND 500 AND legacy_key = btrim(legacy_key)),
    CONSTRAINT legacy_notification_suppressions_destination_type_check
        CHECK (
            length(destination_type) BETWEEN 1 AND 64
            AND destination_type = lower(destination_type)
            AND destination_type !~ '[^a-z0-9_]'
            AND left(destination_type, 1) ~ '[a-z]'
        ),
    CONSTRAINT legacy_notification_suppressions_destination_id_check
        CHECK (
            destination_id IS NULL
            OR (length(destination_id) BETWEEN 1 AND 255 AND destination_id = btrim(destination_id))
        ),
    CONSTRAINT legacy_notification_suppressions_source_type_check
        CHECK (
            source_type IS NULL
            OR (
                length(source_type) BETWEEN 1 AND 64
                AND source_type = lower(source_type)
                AND source_type !~ '[^a-z0-9_]'
                AND left(source_type, 1) ~ '[a-z]'
            )
        ),
    CONSTRAINT legacy_notification_suppressions_source_key_check
        CHECK (
            source_key IS NULL
            OR (length(source_key) BETWEEN 1 AND 255 AND source_key = btrim(source_key))
        ),
    CONSTRAINT legacy_notification_suppressions_source_pair_check
        CHECK ((source_type IS NULL) = (source_key IS NULL)),
    CONSTRAINT legacy_notification_suppressions_provenance_check
        CHECK (length(provenance) BETWEEN 1 AND 128 AND provenance = btrim(provenance)),
    CONSTRAINT legacy_notification_suppressions_batch_check
        CHECK (length(migration_batch_id) BETWEEN 1 AND 128 AND migration_batch_id = btrim(migration_batch_id)),
    CONSTRAINT legacy_notification_suppressions_status_check
        CHECK (resolution_status IN ('resolved_suppressed', 'unresolved', 'ambiguous', 'destination_unverified')),
    CONSTRAINT legacy_notification_suppressions_verification_check
        CHECK (destination_verified_at IS NULL OR destination_id IS NOT NULL),
    CONSTRAINT legacy_notification_suppressions_resolved_check
        CHECK (
            resolution_status <> 'resolved_suppressed'
            OR (
                destination_verified_at IS NOT NULL
                AND destination_id IS NOT NULL
                AND source_type IS NOT NULL
                AND source_key IS NOT NULL
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS legacy_notification_suppressions_import_unique
    ON legacy_notification_suppressions (
        migration_batch_id,
        legacy_key,
        destination_type,
        COALESCE(destination_id, '')
    );

CREATE INDEX IF NOT EXISTS legacy_notification_suppressions_lookup_idx
    ON legacy_notification_suppressions (
        destination_type,
        destination_id,
        source_type,
        source_key
    )
    WHERE resolution_status = 'resolved_suppressed';

CREATE INDEX IF NOT EXISTS legacy_notification_suppressions_unresolved_idx
    ON legacy_notification_suppressions (resolution_status, destination_type, destination_id, id)
    WHERE resolution_status <> 'resolved_suppressed';
