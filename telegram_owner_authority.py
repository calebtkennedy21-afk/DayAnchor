"""One-shot owner-UI authority scoped to one Telegram authorization change.

This provider validates the current Streamlit OIDC principal, but Python
callers and repository authority objects are not an unforgeable security
boundary. Only trusted UI composition may pass this provider to the repository.
"""

from collections.abc import Mapping
import os
from threading import Lock
import time
from uuid import UUID

from streamlit.errors import StreamlitSecretNotFoundError

import auth_boundary
import telegram_authorization
import telegram_identity


_RESOURCE_FIELDS = {
    "approve_destination": frozenset(
        {"bot_id", "destination_type", "destination_id"}
    ),
    "revoke_destination": frozenset(
        {"binding_id", "bot_id", "destination_type", "destination_id"}
    ),
    "approve_sender": frozenset(
        {
            "binding_id",
            "bot_id",
            "destination_type",
            "destination_id",
            "sender_user_id",
        }
    ),
    "revoke_sender": frozenset(
        {
            "binding_id",
            "bot_id",
            "destination_type",
            "destination_id",
            "sender_user_id",
        }
    ),
}
MAX_AUTHORITY_AGE_SECONDS = 60


def _normalize_resource(action, resource):
    if not isinstance(action, str):
        return None
    fields = _RESOURCE_FIELDS.get(action)
    if fields is None or not isinstance(resource, Mapping) or set(resource) != fields:
        return None
    if any(not isinstance(value, str) for value in resource.values()):
        return None

    normalized = dict(resource)
    bot_id = telegram_identity.normalize_verified_bot_id(normalized["bot_id"])
    destination_id = telegram_identity.normalize_telegram_destination_id(
        normalized["destination_id"]
    )
    if (
        bot_id != normalized["bot_id"]
        or destination_id != normalized["destination_id"]
        or normalized["destination_type"] != "telegram"
    ):
        return None
    if "sender_user_id" in normalized:
        sender_value = normalized["sender_user_id"]
        sender_id = (
            telegram_identity.normalize_authorized_sender_id(int(sender_value))
            if sender_value.isascii() and sender_value.isdigit()
            else None
        )
        if sender_id != normalized["sender_user_id"]:
            return None
    if "binding_id" in normalized:
        try:
            binding_id = UUID(normalized["binding_id"])
        except (TypeError, ValueError, AttributeError):
            return None
        if str(binding_id) != normalized["binding_id"]:
            return None
    return tuple(sorted(normalized.items()))


def _current_streamlit_context():
    """Return Streamlit's current script context, never a session-state claim."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        import streamlit as st

        if get_script_run_ctx(suppress_warning=True) is None:
            return None
        return st, os.environ
    except Exception:
        return None


def _current_owner_actor():
    context = _current_streamlit_context()
    if context is None:
        return None
    st, environ = context
    try:
        auth = st.secrets.get("auth")
        owner_subject = environ.get("DAYANCHOR_OWNER_GOOGLE_SUB")
        if (
            st.get_option("server.trustedUserHeaders")
            or not auth_boundary.valid_configuration(auth, owner_subject)
        ):
            return None
        identity = st.user
        if not isinstance(identity, Mapping):
            return None
        if not auth_boundary.authorized_identity(
            identity, owner_subject, auth["client_id"]
        ):
            return None
        subject = identity.get("sub")
        if not isinstance(subject, str) or len(subject) > telegram_authorization.MAX_ACTOR_ID_LENGTH:
            return None
        return subject
    except (StreamlitSecretNotFoundError, KeyError, TypeError, ValueError):
        return None
    except Exception:
        return None


class StreamlitOwnerAuthority(telegram_authorization.OwnerAuthorizationAuthority):
    """Authorize exactly one recent operation for the current configured owner."""

    def __init__(self, *, action, resource):
        scope = _normalize_resource(action, resource)
        if scope is None:
            raise ValueError("A supported Telegram operation and exact resource are required.")
        self._action = action
        self._resource_scope = scope
        self._created_at = time.monotonic()
        self._lock = Lock()
        self._consumed = False

    def authorize_telegram_change(self, action, resource):
        scope = _normalize_resource(action, resource)
        if action != self._action or scope != self._resource_scope:
            return None

        with self._lock:
            if self._consumed:
                return None
            self._consumed = True
            if time.monotonic() - self._created_at > MAX_AUTHORITY_AGE_SECONDS:
                return None
            actor = _current_owner_actor()
            if actor is None:
                return None
            return telegram_authorization.OwnerAuthorizationGrant(
                actor_identity=actor,
                provenance="authenticated_owner_ui",
            )
