CREATE TABLE IF NOT EXISTS notification_worker_runs (
    id BIGSERIAL PRIMARY KEY,
    worker_id TEXT NOT NULL,
    run_id UUID NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    outcome TEXT,
    error_details TEXT,
    CONSTRAINT notification_worker_runs_identity_unique UNIQUE (worker_id, run_id),
    CONSTRAINT notification_worker_runs_worker_id_check
        CHECK (
            length(worker_id) BETWEEN 1 AND 128
            AND worker_id = btrim(worker_id)
            AND worker_id !~ '[[:cntrl:]]'
        ),
    CONSTRAINT notification_worker_runs_outcome_check
        CHECK (outcome IS NULL OR outcome IN ('success', 'failed')),
    CONSTRAINT notification_worker_runs_completion_check
        CHECK (
            (completed_at IS NULL AND outcome IS NULL AND error_details IS NULL)
            OR (completed_at IS NOT NULL AND outcome IS NOT NULL)
        ),
    CONSTRAINT notification_worker_runs_success_error_check
        CHECK (outcome <> 'success' OR error_details IS NULL),
    CONSTRAINT notification_worker_runs_error_length_check
        CHECK (error_details IS NULL OR length(error_details) <= 1000)
);

CREATE INDEX IF NOT EXISTS notification_worker_runs_latest_idx
    ON notification_worker_runs (worker_id, started_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS telegram_poll_cursors (
    bot_id TEXT PRIMARY KEY,
    destination_type TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    bot_identity_provenance TEXT NOT NULL,
    destination_binding_provenance TEXT NOT NULL,
    bot_identity_verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    destination_binding_verified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_update_id BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT telegram_poll_cursors_bot_id_check
        CHECK (bot_id ~ '^[1-9][0-9]{0,19}$'),
    CONSTRAINT telegram_poll_cursors_destination_type_check
        CHECK (destination_type = 'telegram'),
    CONSTRAINT telegram_poll_cursors_destination_id_check
        CHECK (length(destination_id) BETWEEN 1 AND 255 AND destination_id = btrim(destination_id)),
    CONSTRAINT telegram_poll_cursors_bot_provenance_check
        CHECK (
            length(bot_identity_provenance) BETWEEN 1 AND 128
            AND bot_identity_provenance = btrim(bot_identity_provenance)
        ),
    CONSTRAINT telegram_poll_cursors_binding_provenance_check
        CHECK (
            length(destination_binding_provenance) BETWEEN 1 AND 128
            AND destination_binding_provenance = btrim(destination_binding_provenance)
        ),
    CONSTRAINT telegram_poll_cursors_next_update_check CHECK (next_update_id >= 0)
);

CREATE TABLE IF NOT EXISTS telegram_update_receipts (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL
        REFERENCES telegram_poll_cursors (bot_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    update_id BIGINT NOT NULL,
    destination_type TEXT NOT NULL,
    addressed_destination_id TEXT,
    outcome TEXT NOT NULL,
    outcome_detail TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT telegram_update_receipts_identity_unique UNIQUE (bot_id, update_id),
    CONSTRAINT telegram_update_receipts_update_id_check CHECK (update_id >= 0),
    CONSTRAINT telegram_update_receipts_destination_type_check
        CHECK (destination_type = 'telegram'),
    CONSTRAINT telegram_update_receipts_destination_id_check
        CHECK (
            addressed_destination_id IS NULL
            OR (
                length(addressed_destination_id) BETWEEN 1 AND 255
                AND addressed_destination_id = btrim(addressed_destination_id)
            )
        ),
    CONSTRAINT telegram_update_receipts_outcome_check
        CHECK (outcome IN ('applied', 'ignored', 'rejected')),
    CONSTRAINT telegram_update_receipts_detail_length_check
        CHECK (outcome_detail IS NULL OR length(outcome_detail) <= 500)
);

CREATE INDEX IF NOT EXISTS telegram_update_receipts_destination_idx
    ON telegram_update_receipts (bot_id, addressed_destination_id, update_id DESC);

CREATE INDEX IF NOT EXISTS telegram_update_receipts_outcome_idx
    ON telegram_update_receipts (bot_id, outcome, processed_at DESC);
