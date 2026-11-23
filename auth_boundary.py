"""Fail-closed owner authorization using Streamlit's verified OIDC identity."""

import re
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

from streamlit.errors import StreamlitSecretNotFoundError


GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
SUBJECT_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,255}\Z")
AUTH_WIDGET_KEYS = frozenset({"dayanchor_auth_login", "dayanchor_auth_logout"})


def valid_configuration(auth, owner_subject):
    if not isinstance(auth, Mapping):
        return False
    if not isinstance(owner_subject, str) or not SUBJECT_PATTERN.fullmatch(owner_subject):
        return False
    for key in ("client_id", "client_secret", "cookie_secret", "redirect_uri"):
        value = auth.get(key)
        if not isinstance(value, str) or not value or value != value.strip():
            return False
    if len(auth["cookie_secret"]) < 32 or auth.get("server_metadata_url") != GOOGLE_METADATA_URL:
        return False
    try:
        redirect = urlsplit(auth["redirect_uri"])
        port = redirect.port
    except ValueError:
        return False
    if redirect.scheme == "https":
        valid_origin = bool(redirect.hostname)
    else:
        valid_origin = redirect.scheme == "http" and redirect.hostname in ("localhost", "127.0.0.1", "::1")
    return bool(
        valid_origin
        and redirect.netloc
        and redirect.username is None
        and redirect.password is None
        and (port is None or 0 < port <= 65535)
        and redirect.path == "/oauth2callback"
        and not redirect.query
        and not redirect.fragment
    )


def authorized_identity(identity, owner_subject, client_id, now=None):
    if not isinstance(identity, Mapping):
        return False
    if identity.get("is_logged_in") is not True:
        return False
    subject = identity.get("sub")
    issuer = identity.get("iss")
    audience = identity.get("aud")
    expires = identity.get("exp")
    if not isinstance(subject, str) or not SUBJECT_PATTERN.fullmatch(subject):
        return False
    if not isinstance(issuer, str) or issuer not in GOOGLE_ISSUERS:
        return False
    if isinstance(audience, str):
        audience_matches = audience == client_id
    elif isinstance(audience, (list, tuple)):
        audience_matches = all(isinstance(item, str) for item in audience) and client_id in audience
    else:
        audience_matches = False
    current_time = time.time() if now is None else now
    return (
        subject == owner_subject
        and audience_matches
        and type(expires) is int
        and expires > current_time
    )


def clear_application_session(session_state):
    for key in list(session_state):
        if key not in AUTH_WIDGET_KEYS:
            del session_state[key]


def logout(st):
    clear_application_session(st.session_state)
    st.logout()


def enforce_owner_access(st, environ):
    try:
        auth = st.secrets.get("auth")
    except StreamlitSecretNotFoundError:
        auth = None

    owner_subject = environ.get("DAYANCHOR_OWNER_GOOGLE_SUB")
    if st.get_option("server.trustedUserHeaders") or not valid_configuration(auth, owner_subject):
        clear_application_session(st.session_state)
        st.error("Authentication is not configured correctly. Contact the application owner.")
        return False

    identity = st.user
    if not authorized_identity(identity, owner_subject, auth["client_id"]):
        clear_application_session(st.session_state)
        if identity.get("is_logged_in") is True:
            st.error("This account is not authorized or its sign-in has expired.")
            if st.button("Sign out", key="dayanchor_auth_logout"):
                logout(st)
        else:
            st.info("Sign in with the authorized Google account to continue.")
            if st.button("Sign in with Google", key="dayanchor_auth_login"):
                st.login()
        return False

    if st.sidebar.button("Sign out", key="dayanchor_auth_logout"):
        logout(st)
        return False
    return True
