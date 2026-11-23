CREATE TABLE IF NOT EXISTS telegram_destination_bindings (
    binding_id UUID PRIMARY KEY,
    bot_id TEXT NOT NULL,
    destination_type TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    approval_status TEXT NOT NULL DEFAULT 'approved',
    bot_identity_verified_at TIMESTAMPTZ NOT NULL,
    bot_identity_provenance TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approver_actor_id TEXT NOT NULL,
    approval_provenance TEXT NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_by_actor_id TEXT,
    revocation_provenance TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT telegram_destination_bindings_bot_id_check
        CHECK (bot_id ~ '^[1-9][0-9]{0,19}$'),
    CONSTRAINT telegram_destination_bindings_destination_type_check
        CHECK (destination_type = 'telegram'),
    CONSTRAINT telegram_destination_bindings_destination_id_check
        CHECK (destination_id ~ '^-?[1-9][0-9]{0,19}$'),
    CONSTRAINT telegram_destination_bindings_status_check
        CHECK (approval_status IN ('approved', 'revoked')),
    CONSTRAINT telegram_destination_bindings_bot_provenance_check
        CHECK (bot_identity_provenance = 'telegram_get_me'),
    CONSTRAINT telegram_destination_bindings_actor_check
        CHECK (
            length(approver_actor_id) BETWEEN 1 AND 128
            AND approver_actor_id = btrim(approver_actor_id)
            AND approver_actor_id !~ '[[:cntrl:]]'
        ),
    CONSTRAINT telegram_destination_bindings_approval_provenance_check
        CHECK (approval_provenance IN ('authenticated_owner_ui', 'authenticated_operator_cli')),
    CONSTRAINT telegram_destination_bindings_revocation_check
        CHECK (
            (approval_status = 'approved'
                AND revoked_at IS NULL
                AND revoked_by_actor_id IS NULL
                AND revocation_provenance IS NULL)
            OR
            (approval_status = 'revoked'
                AND revoked_at IS NOT NULL
                AND revoked_by_actor_id IS NOT NULL
                AND revocation_provenance IS NOT NULL
                AND revocation_provenance IN (
                    'authenticated_owner_ui',
                    'authenticated_operator_cli'
                ))
        ),
    CONSTRAINT telegram_destination_bindings_revoked_actor_check
        CHECK (
            revoked_by_actor_id IS NULL
            OR (
                length(revoked_by_actor_id) BETWEEN 1 AND 128
                AND revoked_by_actor_id = btrim(revoked_by_actor_id)
                AND revoked_by_actor_id !~ '[[:cntrl:]]'
            )
        ),
    CONSTRAINT telegram_destination_bindings_approval_actor_unique
        UNIQUE (binding_id, bot_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS telegram_destination_bindings_one_active_bot_idx
    ON telegram_destination_bindings (bot_id)
    WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS telegram_destination_bindings_history_idx
    ON telegram_destination_bindings (bot_id, approved_at DESC, binding_id);

CREATE TABLE IF NOT EXISTS telegram_authorized_senders (
    sender_approval_id UUID PRIMARY KEY,
    binding_id UUID NOT NULL,
    bot_id TEXT NOT NULL,
    sender_user_id TEXT NOT NULL,
    approval_status TEXT NOT NULL DEFAULT 'approved',
    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approver_actor_id TEXT NOT NULL,
    approval_provenance TEXT NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_by_actor_id TEXT,
    revocation_provenance TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT telegram_authorized_senders_binding_fk
        FOREIGN KEY (binding_id, bot_id)
        REFERENCES telegram_destination_bindings (binding_id, bot_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT telegram_authorized_senders_sender_id_check
        CHECK (sender_user_id ~ '^[1-9][0-9]{0,19}$'),
    CONSTRAINT telegram_authorized_senders_status_check
        CHECK (approval_status IN ('approved', 'revoked')),
    CONSTRAINT telegram_authorized_senders_actor_check
        CHECK (
            length(approver_actor_id) BETWEEN 1 AND 128
            AND approver_actor_id = btrim(approver_actor_id)
            AND approver_actor_id !~ '[[:cntrl:]]'
        ),
    CONSTRAINT telegram_authorized_senders_approval_provenance_check
        CHECK (approval_provenance IN ('authenticated_owner_ui', 'authenticated_operator_cli')),
    CONSTRAINT telegram_authorized_senders_revocation_check
        CHECK (
            (approval_status = 'approved'
                AND revoked_at IS NULL
                AND revoked_by_actor_id IS NULL
                AND revocation_provenance IS NULL)
            OR
            (approval_status = 'revoked'
                AND revoked_at IS NOT NULL
                AND revoked_by_actor_id IS NOT NULL
                AND revocation_provenance IS NOT NULL
                AND revocation_provenance IN (
                    'authenticated_owner_ui',
                    'authenticated_operator_cli'
                ))
        ),
    CONSTRAINT telegram_authorized_senders_revoked_actor_check
        CHECK (
            revoked_by_actor_id IS NULL
            OR (
                length(revoked_by_actor_id) BETWEEN 1 AND 128
                AND revoked_by_actor_id = btrim(revoked_by_actor_id)
                AND revoked_by_actor_id !~ '[[:cntrl:]]'
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS telegram_authorized_senders_one_active_idx
    ON telegram_authorized_senders (binding_id, sender_user_id)
    WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS telegram_authorized_senders_lookup_idx
    ON telegram_authorized_senders (bot_id, binding_id, sender_user_id)
    WHERE approval_status = 'approved';

CREATE INDEX IF NOT EXISTS telegram_authorized_senders_history_idx
    ON telegram_authorized_senders (binding_id, approved_at DESC, sender_approval_id);
