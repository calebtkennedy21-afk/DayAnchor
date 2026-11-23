from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import auth_boundary
import telegram_authorization
import telegram_owner_authority as owner_authority
import telegram_identity


OWNER = "synthetic-google-subject"
CLIENT_ID = "synthetic-google-client"
AUTH = {
    "client_id": CLIENT_ID,
    "client_secret": "synthetic-secret",
    "cookie_secret": "x" * 40,
    "redirect_uri": "https://example.test/oauth2callback",
    "server_metadata_url": auth_boundary.GOOGLE_METADATA_URL,
}
RESOURCE = {
    "bot_id": "123456",
    "destination_type": "telegram",
    "destination_id": "555",
}
SENDER_RESOURCE = {
    **RESOURCE,
    "binding_id": "cf85d414-b705-43ec-af66-a938eaec71fc",
    "sender_user_id": "777",
}
_DEFAULT_IDENTITY = object()


def _identity(**overrides):
    claims = {
        "is_logged_in": True,
        "sub": OWNER,
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": int(datetime.now(timezone.utc).timestamp()) + 600,
    }
    claims.update(overrides)
    return claims


def _context(
    identity=_DEFAULT_IDENTITY, *, auth=AUTH, owner=OWNER, trusted_headers=None
):
    st = SimpleNamespace(
        secrets={"auth": auth},
        user=_identity() if identity is _DEFAULT_IDENTITY else identity,
        get_option=Mock(return_value=trusted_headers),
    )
    return st, {"DAYANCHOR_OWNER_GOOGLE_SUB": owner}


def _authority(action="approve_destination", resource=RESOURCE):
    return owner_authority.StreamlitOwnerAuthority(action=action, resource=resource)


def _authorize(authority, action="approve_destination", resource=RESOURCE, monkeypatch=None, context=None):
    if monkeypatch is not None:
        monkeypatch.setattr(owner_authority, "_current_streamlit_context", lambda: context)
    return authority.authorize_telegram_change(action, resource)


def test_valid_authenticated_owner_gets_one_operation_scoped_grant(monkeypatch):
    authority = _authority()
    grant = _authorize(authority, monkeypatch=monkeypatch, context=_context())

    assert grant == telegram_authorization.OwnerAuthorizationGrant(
        actor_identity=OWNER,
        provenance="authenticated_owner_ui",
    )
    assert authority.authorize_telegram_change("approve_destination", RESOURCE) is None


@pytest.mark.parametrize(
    "identity",
    [
        {"is_logged_in": False},
        _identity(sub="different-subject"),
        _identity(iss="https://untrusted.example"),
        _identity(aud="different-client"),
        _identity(exp=0),
        None,
    ],
)
def test_missing_or_unapproved_identity_is_denied(monkeypatch, identity):
    authority = _authority()
    context = _context(identity)

    assert _authorize(authority, monkeypatch=monkeypatch, context=context) is None


def test_missing_configuration_and_missing_principal_are_denied(monkeypatch):
    authority = _authority()
    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=_context(auth=None),
    ) is None

    authority = _authority()
    assert _authorize(authority, monkeypatch=monkeypatch, context=None) is None


def test_missing_owner_configuration_and_session_state_claim_are_denied(monkeypatch):
    authority = _authority()
    st, environ = _context()
    environ.pop("DAYANCHOR_OWNER_GOOGLE_SUB")
    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=(st, environ),
    ) is None

    authority = _authority()
    st, environ = _context({"is_logged_in": False})
    st.session_state = {"authenticated": True, "sub": OWNER}
    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=(st, environ),
    ) is None


def test_provider_uses_exact_owner_subject_and_rejects_trusted_headers(monkeypatch):
    authority = _authority()
    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=_context(owner="changed-owner"),
    ) is None

    authority = _authority()
    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=_context(trusted_headers={"x-user": "sub"}),
    ) is None


def test_actor_and_provenance_cannot_be_supplied_to_provider():
    with pytest.raises(TypeError):
        owner_authority.StreamlitOwnerAuthority(
            action="approve_destination",
            resource=RESOURCE,
            actor_identity="forged-owner",
        )
    with pytest.raises(TypeError):
        owner_authority.StreamlitOwnerAuthority(
            action="approve_destination",
            resource=RESOURCE,
            provenance="authenticated_owner_ui",
        )


@pytest.mark.parametrize(
    ("action", "resource"),
    [
        ("arbitrary_database_write", RESOURCE),
        ("approve_destination", {**RESOURCE, "extra": "value"}),
        ("approve_destination", {**RESOURCE, "bot_id": "654321"}),
        ("approve_destination", {**RESOURCE, "destination_id": "556"}),
        ("approve_sender", SENDER_RESOURCE),
        (
            "approve_sender",
            {**SENDER_RESOURCE, "sender_user_id": "888"},
        ),
    ],
)
def test_authority_cannot_be_reused_for_another_operation_or_resource(
    monkeypatch, action, resource
):
    authority = _authority()
    assert _authorize(
        authority,
        action=action,
        resource=resource,
        monkeypatch=monkeypatch,
        context=_context(),
    ) is None
    assert authority.authorize_telegram_change(
        "approve_destination", RESOURCE
    ) is not None


@pytest.mark.parametrize(
    ("action", "resource"),
    [
        ("approve_destination", {**RESOURCE, "bot_id": "0"}),
        ("approve_destination", {**RESOURCE, "destination_id": "abc"}),
        ("approve_sender", {**SENDER_RESOURCE, "sender_user_id": "0"}),
        ("revoke_sender", {**SENDER_RESOURCE, "binding_id": "not-a-uuid"}),
    ],
)
def test_invalid_target_cannot_create_authority(action, resource):
    with pytest.raises(ValueError, match="supported Telegram operation"):
        _authority(action, resource)


@pytest.mark.parametrize(
    ("action", "resource"),
    [
        ("approve_destination", RESOURCE),
        (
            "revoke_destination",
            {**RESOURCE, "binding_id": SENDER_RESOURCE["binding_id"]},
        ),
        ("approve_sender", SENDER_RESOURCE),
        ("revoke_sender", SENDER_RESOURCE),
    ],
)
def test_only_the_four_scoped_operations_can_receive_owner_grants(
    monkeypatch, action, resource
):
    grant = _authorize(
        _authority(action, resource),
        action=action,
        resource=resource,
        monkeypatch=monkeypatch,
        context=_context(),
    )

    assert grant is not None
    assert grant.provenance == "authenticated_owner_ui"


def test_logout_and_owner_configuration_change_before_authorization_deny(monkeypatch):
    authority = _authority()
    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=_context(_identity(is_logged_in=False)),
    ) is None

    authority = _authority()
    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=_context(owner="new-owner"),
    ) is None


def test_identity_expired_before_mutation_boundary_is_denied(monkeypatch):
    authority = _authority()
    context = _context(_identity(exp=1000))
    monkeypatch.setattr(auth_boundary.time, "time", lambda: 1001)

    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=context,
    ) is None


def test_stale_authority_and_replayed_request_are_denied(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(
        owner_authority,
        "time",
        SimpleNamespace(monotonic=lambda: clock[0]),
    )
    authority = _authority()
    clock[0] += owner_authority.MAX_AUTHORITY_AGE_SECONDS + 1
    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=_context(),
    ) is None

    authority = _authority()
    context = _context()
    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=context,
    ) is not None
    assert authority.authorize_telegram_change(
        "approve_destination", RESOURCE
    ) is None


def test_concurrent_authorization_attempts_issue_at_most_one_grant(monkeypatch):
    authority = _authority()
    monkeypatch.setattr(
        owner_authority,
        "_current_streamlit_context",
        lambda: _context(),
    )
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _index: authority.authorize_telegram_change(
                    "approve_destination", RESOURCE
                ),
                range(8),
            )
        )
    grants = [result for result in results if result is not None]

    assert len(grants) == 1
    assert grants[0].actor_identity == OWNER


def test_scheduler_and_inbox_without_streamlit_context_cannot_get_authority(monkeypatch):
    import alert_scheduler
    import telegram_inbox

    assert alert_scheduler is not None
    assert telegram_inbox is not None
    assert owner_authority._current_streamlit_context() is None
    monkeypatch.setattr(owner_authority, "_current_streamlit_context", lambda: None)
    assert _authority().authorize_telegram_change(
        "approve_destination", RESOURCE
    ) is None


def test_telegram_sender_identity_is_not_a_dayanchor_owner_principal(monkeypatch):
    authority = _authority()
    telegram_sender = {"is_bot": False, "id": 777, "first_name": "Telegram sender"}

    assert _authorize(
        authority,
        monkeypatch=monkeypatch,
        context=_context(telegram_sender),
    ) is None


def test_provider_does_not_call_telegram_or_write_approval_storage(monkeypatch):
    import telegram_alerts

    telegram_call = Mock(side_effect=AssertionError("Telegram must not be called"))
    approval_write = Mock(side_effect=AssertionError("Provider must not write approvals"))
    monkeypatch.setattr(telegram_alerts, "call_api", telegram_call)
    monkeypatch.setattr(
        telegram_authorization, "approve_destination_binding", approval_write
    )
    monkeypatch.setattr(
        telegram_authorization, "approve_sender", approval_write
    )
    monkeypatch.setattr(
        telegram_authorization, "revoke_destination_binding", approval_write
    )
    monkeypatch.setattr(telegram_authorization, "revoke_sender", approval_write)

    grant = _authorize(
        _authority(),
        monkeypatch=monkeypatch,
        context=_context(),
    )

    assert grant is not None
    telegram_call.assert_not_called()
    approval_write.assert_not_called()


def test_authentication_boundary_rejects_non_mapping_identity():
    assert not auth_boundary.authorized_identity(None, OWNER, CLIENT_ID)
    assert not auth_boundary.authorized_identity([], OWNER, CLIENT_ID)
