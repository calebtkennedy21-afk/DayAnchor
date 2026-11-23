CREATE TABLE IF NOT EXISTS notification_outbox (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_key TEXT NOT NULL,
    destination_type TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 8,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    claim_owner TEXT,
    claim_token UUID,
    lease_expires_at TIMESTAMPTZ,
    send_started_at TIMESTAMPTZ,
    telegram_message_id BIGINT,
    error_classification TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ,
    uncertain_at TIMESTAMPTZ,
    CONSTRAINT notification_outbox_identity_unique
        UNIQUE (source_type, source_key, destination_type, destination_id),
    CONSTRAINT notification_outbox_source_type_check
        CHECK (
            length(source_type) BETWEEN 1 AND 64
            AND source_type = lower(source_type)
            AND source_type !~ '[^a-z0-9_]'
            AND left(source_type, 1) ~ '[a-z]'
        ),
    CONSTRAINT notification_outbox_source_key_check
        CHECK (length(source_key) BETWEEN 1 AND 255 AND source_key = btrim(source_key)),
    CONSTRAINT notification_outbox_destination_type_check
        CHECK (
            length(destination_type) BETWEEN 1 AND 64
            AND destination_type = lower(destination_type)
            AND destination_type !~ '[^a-z0-9_]'
            AND left(destination_type, 1) ~ '[a-z]'
        ),
    CONSTRAINT notification_outbox_destination_id_check
        CHECK (length(destination_id) BETWEEN 1 AND 255 AND destination_id = btrim(destination_id)),
    CONSTRAINT notification_outbox_status_check
        CHECK (status IN ('pending', 'claimed', 'sending', 'sent', 'failed', 'uncertain', 'dead')),
    CONSTRAINT notification_outbox_attempts_check
        CHECK (attempt_count >= 0 AND max_attempts BETWEEN 1 AND 8 AND attempt_count <= max_attempts),
    CONSTRAINT notification_outbox_payload_check
        CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT notification_outbox_claim_check
        CHECK (
            (status IN ('claimed', 'sending')
                AND claim_owner IS NOT NULL AND claim_token IS NOT NULL AND lease_expires_at IS NOT NULL)
            OR
            (status NOT IN ('claimed', 'sending')
                AND claim_owner IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL)
        ),
    CONSTRAINT notification_outbox_sent_check
        CHECK (status <> 'sent' OR (sent_at IS NOT NULL AND telegram_message_id IS NOT NULL)),
    CONSTRAINT notification_outbox_uncertain_check
        CHECK (status <> 'uncertain' OR uncertain_at IS NOT NULL),
    CONSTRAINT notification_outbox_send_start_check
        CHECK (attempt_count = 0 OR send_started_at IS NOT NULL),
    CONSTRAINT notification_outbox_message_id_check
        CHECK (telegram_message_id IS NULL OR telegram_message_id > 0),
    CONSTRAINT notification_outbox_error_classification_check
        CHECK (
            error_classification IS NULL
            OR error_classification IN ('transient', 'rate_limited', 'permanent', 'unknown', 'uncertain', 'expired')
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS notification_outbox_claim_token_unique
    ON notification_outbox (claim_token)
    WHERE claim_token IS NOT NULL;

CREATE INDEX IF NOT EXISTS notification_outbox_eligibility_idx
    ON notification_outbox (available_at, id)
    WHERE status IN ('pending', 'failed');

CREATE INDEX IF NOT EXISTS notification_outbox_lease_recovery_idx
    ON notification_outbox (lease_expires_at, id)
    WHERE status IN ('claimed', 'sending');
