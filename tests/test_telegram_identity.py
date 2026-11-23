from datetime import datetime, timedelta, timezone
from urllib.error import URLError

import pytest

import notification_operational_state
import telegram_identity as identity
import telegram_inbox


NOW = datetime(2026, 11, 22, 23, 0, tzinfo=timezone.utc)
BOT_TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk"


def _verified(bot_id=123456, *, token=BOT_TOKEN, when=NOW):
    return identity.verify_bot_identity(
        token,
        lambda _token: {"ok": True, "result": {"id": bot_id, "is_bot": True}},
        now=when,
    )


def _message(*, chat_id=555, sender_id=777):
    return {
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": sender_id},
            "text": "undo",
        }
    }


def _authorize(update=None, **overrides):
    values = {
        "identity": _verified(),
        "configured_destination_id": "555",
        "approved_destination_id": "555",
        "destination_approved": True,
        "approved_sender_ids": [777],
        "update": update or _message(),
        "now": NOW,
    }
    values.update(overrides)
    return identity.authorize_inbound_update(**values)


def test_valid_authenticated_bot_response_is_strictly_validated():
    result = identity.validate_get_me_response(
        {"ok": True, "result": {"id": 123456789, "is_bot": True, "username": "dayanchor_bot"}}
    )

    assert result.status is identity.VerificationStatus.VERIFIED
    assert result.bot_id == "123456789"
    assert result.verified_at is None


def test_invalid_token_response_returns_safe_diagnostic():
    result = identity.validate_get_me_response(
        {"ok": False, "error_code": 401, "description": "credential text"}
    )

    assert result.status is identity.VerificationStatus.INVALID_CREDENTIALS
    assert result.diagnostic_code == "telegram_401"
    assert "credential text" not in repr(result)


def test_transport_failure_does_not_expose_exception_or_token():
    def unavailable(token):
        raise URLError(f"failed request for {token}")

    result = identity.verify_bot_identity(BOT_TOKEN, unavailable, now=NOW)

    assert result.status is identity.VerificationStatus.TRANSPORT_UNAVAILABLE
    assert BOT_TOKEN not in repr(result)
    assert "failed request" not in repr(result)


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {"result": {"id": 1, "is_bot": True}},
        {"ok": 1, "result": {"id": 1, "is_bot": True}},
        {"ok": True, "result": []},
    ],
)
def test_malformed_get_me_envelopes_are_rejected(response):
    assert identity.validate_get_me_response(response).status is identity.VerificationStatus.MALFORMED_RESPONSE


@pytest.mark.parametrize("bot_id", [0, -1, "123", 10**20])
def test_invalid_or_non_integer_bot_ids_are_rejected(bot_id):
    result = identity.validate_get_me_response(
        {"ok": True, "result": {"id": bot_id, "is_bot": True}}
    )

    assert result.status is identity.VerificationStatus.INVALID_IDENTITY
    assert result.bot_id is None


def test_boolean_bot_id_is_rejected():
    result = identity.validate_get_me_response(
        {"ok": True, "result": {"id": True, "is_bot": True}}
    )

    assert result.status is identity.VerificationStatus.INVALID_IDENTITY


def test_missing_or_false_is_bot_is_rejected():
    for result_payload in ({"id": 123456}, {"id": 123456, "is_bot": False}):
        result = identity.validate_get_me_response({"ok": True, "result": result_payload})
        assert result.status is identity.VerificationStatus.INVALID_IDENTITY


def test_verification_requires_a_token_and_uses_only_injected_transport():
    calls = []
    result = identity.verify_bot_identity(
        "",
        lambda token: calls.append(token) or {"ok": True, "result": {"id": 123456, "is_bot": True}},
        now=NOW,
    )

    assert result.status is identity.VerificationStatus.MISSING_TOKEN
    assert calls == []
    verified = identity.verify_bot_identity(
        BOT_TOKEN,
        lambda token: calls.append(token) or {"ok": True, "result": {"id": 123456, "is_bot": True}},
        now=NOW,
    )
    assert verified.is_verified
    assert verified.verified_at == NOW
    assert calls == [BOT_TOKEN]


def test_same_bot_after_token_rotation_can_reuse_identity_cursor():
    assessment = identity.compare_identity_configuration(
        _verified(token="rotated credential"),
        previous_bot_id="123456",
        configured_destination_id="555",
        previously_approved_destination_id="555",
        token_rotated=True,
        now=NOW,
    )

    assert assessment.status_codes == ("token_rotation_same_bot",)
    assert assessment.cursor_reusable
    assert assessment.activation_eligible


def test_different_bot_requires_explicit_replacement_approval_and_never_reuses_cursor():
    unapproved = identity.compare_identity_configuration(
        _verified(bot_id=654321),
        previous_bot_id="123456",
        configured_destination_id="555",
        previously_approved_destination_id="555",
        now=NOW,
    )
    approved = identity.compare_identity_configuration(
        _verified(bot_id=654321),
        previous_bot_id="123456",
        configured_destination_id="555",
        previously_approved_destination_id="555",
        replacement_approved=True,
        now=NOW,
    )

    assert "replacement_requires_approval" in unapproved.status_codes
    assert not unapproved.activation_eligible
    assert "replacement_approved_new_cursor_required" in approved.status_codes
    assert approved.activation_eligible
    assert not approved.cursor_reusable


def test_changed_destination_requires_separate_approval():
    unapproved = identity.compare_identity_configuration(
        _verified(),
        previous_bot_id="123456",
        configured_destination_id="-555",
        previously_approved_destination_id="555",
        now=NOW,
    )
    approved = identity.compare_identity_configuration(
        _verified(),
        previous_bot_id="123456",
        configured_destination_id="-555",
        previously_approved_destination_id="555",
        destination_change_approved=True,
        now=NOW,
    )

    assert "destination_change_requires_approval" in unapproved.status_codes
    assert not unapproved.activation_eligible
    assert "destination_change_approved_rebinding_required" in approved.status_codes
    assert approved.activation_eligible
    assert not approved.cursor_reusable


def test_missing_initial_approval_and_stale_verification_do_not_activate():
    initial = identity.compare_identity_configuration(
        _verified(),
        previous_bot_id=None,
        configured_destination_id="555",
        previously_approved_destination_id=None,
        now=NOW,
    )
    stale = identity.compare_identity_configuration(
        _verified(when=NOW - timedelta(days=31)),
        previous_bot_id="123456",
        configured_destination_id="555",
        previously_approved_destination_id="555",
        now=NOW,
    )

    assert not initial.activation_eligible
    assert "initial_identity_approval_required" in initial.status_codes
    assert "bot_identity_stale" in stale.status_codes
    assert not stale.activation_eligible


def test_approved_destination_and_sender_are_both_required():
    assert _authorize().allowed
    assert _authorize(destination_approved=False).reason_code == "destination_unapproved"
    assert _authorize(approved_destination_id="556").reason_code == "destination_mismatch"
    assert _authorize(approved_sender_ids=None).reason_code == "sender_authorization_missing"


def test_unapproved_destination_is_rejected():
    result = _authorize(update=_message(chat_id=999))

    assert not result.allowed
    assert result.reason_code == "destination_mismatch"


def test_malformed_string_chat_identity_is_rejected():
    result = _authorize(update=_message(chat_id="555"))

    assert not result.allowed
    assert result.reason_code == "malformed_identity"


def test_unauthorized_sender_in_approved_group_is_rejected():
    result = _authorize(
        update=_message(chat_id=-100555, sender_id=888),
        configured_destination_id="-100555",
        approved_destination_id="-100555",
    )

    assert not result.allowed
    assert result.reason_code == "sender_unapproved"


def test_missing_or_malformed_sender_identity_is_rejected():
    missing = {"message": {"chat": {"id": 555}, "text": "undo"}}
    malformed = _message(sender_id=True)

    assert _authorize(update=missing).reason_code == "malformed_update"
    assert _authorize(update=malformed).reason_code == "malformed_identity"


def test_private_chat_does_not_implicitly_authorize_chat_owner():
    private_chat = _message(chat_id=777, sender_id=777)

    assert _authorize(
        update=private_chat,
        configured_destination_id="777",
        approved_destination_id="777",
        approved_sender_ids=[],
    ).reason_code == "sender_authorization_missing"
    assert _authorize(
        update=private_chat,
        configured_destination_id="777",
        approved_destination_id="777",
        approved_sender_ids=[777],
    ).allowed


def test_configuration_assessment_reports_conflicts_without_exposing_values():
    assessment = identity.assess_telegram_configuration(
        {"telegram_bot_token": "settings-token", "telegram_chat_id": "555"},
        {"TELEGRAM_BOT_TOKEN": "environment-token", "TELEGRAM_CHAT_ID": "556"},
        identity=_verified(),
        approved_destination_id="555",
        destination_approved=True,
        approved_sender_ids=[777],
        now=NOW,
    )

    assert assessment.bot_token_present
    assert assessment.destination_present
    assert assessment.configuration_conflicts == ("bot_token", "destination")
    assert not assessment.ready
    assert "settings-token" not in repr(assessment)
    assert "environment-token" not in repr(assessment)


def test_configuration_assessment_reports_all_missing_prerequisites():
    assessment = identity.assess_telegram_configuration(
        {},
        {},
        identity=None,
        approved_destination_id=None,
        destination_approved=False,
        approved_sender_ids=None,
        now=NOW,
    )

    assert not assessment.bot_token_present
    assert not assessment.destination_present
    assert assessment.missing_approved_destination
    assert assessment.missing_sender_authorization
    assert assessment.unverified_bot_identity
    assert not assessment.ready


def test_invalid_configuration_values_fail_closed():
    assessment = identity.assess_telegram_configuration(
        {"telegram_chat_id": "@mutable_name"},
        {},
        identity=_verified(),
        approved_destination_id="@mutable_name",
        destination_approved=True,
        approved_sender_ids=["not-a-number"],
        now=NOW,
    )

    assert not assessment.destination_present
    assert assessment.missing_approved_destination
    assert assessment.missing_sender_authorization
    assert not assessment.ready


def test_invalid_higher_precedence_destination_does_not_fall_back():
    assessment = identity.assess_telegram_configuration(
        {"telegram_chat_id": "@mutable_name"},
        {"TELEGRAM_CHAT_ID": "555"},
        identity=_verified(),
        approved_destination_id="555",
        destination_approved=True,
        approved_sender_ids=[777],
        now=NOW,
    )

    assert not assessment.destination_present
    assert assessment.configuration_conflicts == ("destination",)
    assert not assessment.ready


def test_blank_higher_precedence_token_does_not_silently_use_settings_token():
    assessment = identity.assess_telegram_configuration(
        {"telegram_bot_token": "settings-token"},
        {"TELEGRAM_BOT_TOKEN": "   "},
        identity=_verified(),
        approved_destination_id="555",
        destination_approved=True,
        approved_sender_ids=[777],
        now=NOW,
    )

    assert not assessment.bot_token_present
    assert not assessment.ready


def test_identity_primitives_never_provision_cursor_or_invoke_active_inbox(monkeypatch):
    provisioning_calls = []
    inbox_calls = []
    monkeypatch.setattr(
        notification_operational_state,
        "initialize_verified_bot_cursor",
        lambda *_args, **_kwargs: provisioning_calls.append(True),
    )
    monkeypatch.setattr(
        telegram_inbox,
        "process_updates",
        lambda *_args, **_kwargs: inbox_calls.append(True),
    )

    verified = _verified()
    result = _authorize(identity=verified)
    comparison = identity.compare_identity_configuration(
        verified,
        previous_bot_id="123456",
        configured_destination_id="555",
        previously_approved_destination_id="555",
        now=NOW,
    )

    assert result.allowed and comparison.cursor_reusable
    assert provisioning_calls == []
    assert inbox_calls == []


def test_identity_authorization_does_not_change_existing_inbox_parser():
    action = telegram_inbox.parse_update(
        {"message": {"chat": {"id": 555}, "text": "undo"}},
        "555",
    )

    assert action == {"kind": "text", "action": "undo_latest"}
