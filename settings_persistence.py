"""Shared compare-and-swap primitive for durable app-settings payload writes."""


def compare_and_swap_payload(cursor, payload_text, expected_payload):
    """Write a serialized payload only if the stored payload still matches."""
    if expected_payload is None:
        cursor.execute(
            """
            INSERT INTO app_settings (id, payload, updated_at)
            VALUES (1, %s, NOW())
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """,
            (payload_text,),
        )
    else:
        cursor.execute(
            """
            UPDATE app_settings
            SET payload = %s, updated_at = NOW()
            WHERE id = 1 AND payload = %s
            RETURNING id
            """,
            (payload_text, expected_payload),
        )
    return cursor.fetchone() is not None
