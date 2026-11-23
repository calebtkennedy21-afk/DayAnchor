"""Isolated Telegram bot identity verification and inbound authorization rules."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import re


_BOT_ID_PATTERN = re.compile(r"[1-9][0-9]{0,19}\Z")
_TELEGRAM_CHAT_ID_PATTERN = re.compile(r"-?[1-9][0-9]{0,19}\Z")
DEFAULT_VERIFICATION_MAX_AGE = timedelta(days=30)


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    MISSING_TOKEN = "missing_token"
    INVALID_CREDENTIALS = "invalid_credentials"
    API_REJECTED = "api_rejected"
    TRANSPORT_UNAVAILABLE = "transport_unavailable"
    MALFORMED_RESPONSE = "malformed_response"
    INVALID_IDENTITY = "invalid_identity"


@dataclass(frozen=True)
class BotIdentityVerification:
    status: VerificationStatus
    bot_id: str | None = None
    diagnostic_code: str | None = None
    verified_at: datetime | None = None

    @property
    def is_verified(self) -> bool:
        return self.status is VerificationStatus.VERIFIED and self.bot_id is not None


@dataclass(frozen=True)
class TelegramAuthorizationResult:
    allowed: bool
    reason_code: str
    bot_id: str | None = None
    destination_id: str | None = None
    sender_id: str | None = None


@dataclass(frozen=True)
class TelegramConfigurationAssessment:
    bot_token_present: bool
    destination_present: bool
    configuration_conflicts: tuple[str, ...]
    missing_approved_destination: bool
    missing_sender_authorization: bool
    unverified_bot_identity: bool
    stale_bot_identity: bool

    @property
    def ready(self) -> bool:
        return not any(
            (
                not self.bot_token_present,
                not self.destination_present,
                bool(self.configuration_conflicts),
                self.missing_approved_destination,
                self.missing_sender_authorization,
                self.unverified_bot_identity,
                self.stale_bot_identity,
            )
        )


@dataclass(frozen=True)
class IdentityChangeAssessment:
    status_codes: tuple[str, ...]
    verification_fresh: bool
    bot_id_unchanged: bool
    destination_unchanged: bool
    token_rotated: bool
    cursor_reusable: bool
    activation_eligible: bool


def _normalize_bot_id(value: object) -> str | None:
    if type(value) is not int or value <= 0:
        return None
    normalized = str(value)
    return normalized if _BOT_ID_PATTERN.fullmatch(normalized) else None


def _normalize_configured_bot_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if _BOT_ID_PATTERN.fullmatch(normalized) else None


def _normalize_destination_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if _TELEGRAM_CHAT_ID_PATTERN.fullmatch(normalized) else None


def _normalize_sender_id(value: object) -> str | None:
    return _normalize_bot_id(value)


def normalize_telegram_destination_id(value: object) -> str | None:
    """Normalize a configured numeric Telegram destination without accepting booleans."""
    return _normalize_destination_id(value)


def normalize_authorized_sender_id(value: object) -> str | None:
    """Normalize a Telegram user ID using the repository-compatible numeric format."""
    return _normalize_sender_id(value)


def normalize_verified_bot_id(value: object) -> str | None:
    """Normalize a bot ID using the operational-state repository's constraints."""
    return _normalize_configured_bot_id(value)


def _verification_is_fresh(
    verification: BotIdentityVerification | None,
    *,
    now: datetime,
    max_age: timedelta,
) -> bool:
    if (
        not isinstance(verification, BotIdentityVerification)
        or not verification.is_verified
        or verification.verified_at is None
    ):
        return False
    if (
        not isinstance(now, datetime)
        or now.tzinfo is None
        or now.utcoffset() is None
        or not isinstance(max_age, timedelta)
        or max_age < timedelta(0)
    ):
        return False
    verified_at = verification.verified_at
    if (
        not isinstance(verified_at, datetime)
        or verified_at.tzinfo is None
        or verified_at.utcoffset() is None
    ):
        return False
    age = now.astimezone(timezone.utc) - verified_at.astimezone(timezone.utc)
    return timedelta(0) <= age <= max_age


def get_fresh_verified_bot_id(
    verification: BotIdentityVerification | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_VERIFICATION_MAX_AGE,
) -> str | None:
    """Return a repository-compatible bot ID only for a fresh verified result."""
    current_time = now or datetime.now(timezone.utc)
    if not _verification_is_fresh(
        verification, now=current_time, max_age=max_age
    ):
        return None
    bot_id = _normalize_configured_bot_id(verification.bot_id)
    return bot_id


def validate_get_me_response(response: object) -> BotIdentityVerification:
    """Validate a decoded Telegram getMe envelope without retaining its payload."""
    if not isinstance(response, Mapping):
        return BotIdentityVerification(
            VerificationStatus.MALFORMED_RESPONSE, diagnostic_code="invalid_envelope"
        )

    if response.get("ok") is False:
        error_code = response.get("error_code")
        if type(error_code) is int and error_code == 401:
            return BotIdentityVerification(
                VerificationStatus.INVALID_CREDENTIALS, diagnostic_code="telegram_401"
            )
        return BotIdentityVerification(
            VerificationStatus.API_REJECTED, diagnostic_code="telegram_rejected"
        )
    if response.get("ok") is not True:
        return BotIdentityVerification(
            VerificationStatus.MALFORMED_RESPONSE, diagnostic_code="invalid_ok_field"
        )

    result = response.get("result")
    if not isinstance(result, Mapping):
        return BotIdentityVerification(
            VerificationStatus.MALFORMED_RESPONSE, diagnostic_code="invalid_result"
        )
    if result.get("is_bot") is not True:
        return BotIdentityVerification(
            VerificationStatus.INVALID_IDENTITY, diagnostic_code="not_verified_as_bot"
        )

    bot_id = _normalize_bot_id(result.get("id"))
    if bot_id is None:
        return BotIdentityVerification(
            VerificationStatus.INVALID_IDENTITY, diagnostic_code="invalid_bot_id"
        )
    return BotIdentityVerification(VerificationStatus.VERIFIED, bot_id=bot_id)


def verify_bot_identity(
    token: str,
    transport: Callable[[str], object],
    *,
    now: datetime | None = None,
) -> BotIdentityVerification:
    """Verify using an injected getMe transport; never expose token or exception text."""
    if not isinstance(token, str) or not token.strip():
        return BotIdentityVerification(
            VerificationStatus.MISSING_TOKEN, diagnostic_code="missing_token"
        )
    if not callable(transport):
        raise TypeError("transport must be a callable getMe transport.")

    try:
        response = transport(token.strip())
    except Exception:
        return BotIdentityVerification(
            VerificationStatus.TRANSPORT_UNAVAILABLE, diagnostic_code="transport_error"
        )

    verification = validate_get_me_response(response)
    if not verification.is_verified:
        return verification
    verified_at = now or datetime.now(timezone.utc)
    if verified_at.tzinfo is None or verified_at.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    return BotIdentityVerification(
        VerificationStatus.VERIFIED,
        bot_id=verification.bot_id,
        verified_at=verified_at.astimezone(timezone.utc),
    )


def _sender_ids(values: Iterable[object] | None) -> frozenset[str] | None:
    if values is None or isinstance(values, (str, bytes)):
        return None
    try:
        normalized = [_normalize_sender_id(value) for value in values]
    except TypeError:
        return None
    if not normalized or any(value is None for value in normalized):
        return None
    return frozenset(value for value in normalized if value is not None)


def authorize_inbound_update(
    identity: BotIdentityVerification | None,
    *,
    configured_destination_id: object,
    approved_destination_id: object,
    destination_approved: bool,
    approved_sender_ids: Iterable[object] | None,
    update: object,
    now: datetime | None = None,
    max_verification_age: timedelta = DEFAULT_VERIFICATION_MAX_AGE,
) -> TelegramAuthorizationResult:
    """Require fresh bot verification, exact approved chat, and an approved sender."""
    current_time = now or datetime.now(timezone.utc)
    if not isinstance(identity, BotIdentityVerification) or not identity.is_verified:
        return TelegramAuthorizationResult(False, "bot_identity_unverified")
    bot_id = _normalize_configured_bot_id(identity.bot_id)
    if bot_id is None:
        return TelegramAuthorizationResult(False, "bot_identity_invalid")
    if not _verification_is_fresh(
        identity, now=current_time, max_age=max_verification_age
    ):
        return TelegramAuthorizationResult(False, "bot_identity_stale")

    configured_destination = _normalize_destination_id(configured_destination_id)
    approved_destination = _normalize_destination_id(approved_destination_id)
    if (
        destination_approved is not True
        or configured_destination is None
        or approved_destination is None
    ):
        return TelegramAuthorizationResult(False, "destination_unapproved", bot_id=bot_id)
    if configured_destination != approved_destination:
        return TelegramAuthorizationResult(False, "destination_mismatch", bot_id=bot_id)

    senders = _sender_ids(approved_sender_ids)
    if senders is None:
        return TelegramAuthorizationResult(
            False, "sender_authorization_missing", bot_id=bot_id
        )
    if not isinstance(update, Mapping):
        return TelegramAuthorizationResult(False, "malformed_update", bot_id=bot_id)

    message = update.get("message")
    callback = update.get("callback_query")
    if (message is None) == (callback is None):
        return TelegramAuthorizationResult(False, "malformed_update", bot_id=bot_id)
    if callback is not None:
        if not isinstance(callback, Mapping):
            return TelegramAuthorizationResult(False, "malformed_update", bot_id=bot_id)
        message = callback.get("message")
        sender = callback.get("from")
    else:
        sender = message.get("from") if isinstance(message, Mapping) else None
    if not isinstance(message, Mapping) or not isinstance(sender, Mapping):
        return TelegramAuthorizationResult(False, "malformed_update", bot_id=bot_id)
    chat = message.get("chat")
    if not isinstance(chat, Mapping):
        return TelegramAuthorizationResult(False, "malformed_update", bot_id=bot_id)

    raw_actual_destination = chat.get("id")
    actual_destination = (
        _normalize_destination_id(raw_actual_destination)
        if type(raw_actual_destination) is int
        else None
    )
    actual_sender = _normalize_sender_id(sender.get("id"))
    if actual_destination is None or actual_sender is None:
        return TelegramAuthorizationResult(False, "malformed_identity", bot_id=bot_id)
    if actual_destination != approved_destination:
        return TelegramAuthorizationResult(
            False,
            "destination_mismatch",
            bot_id=bot_id,
            destination_id=actual_destination,
            sender_id=actual_sender,
        )
    if actual_sender not in senders:
        return TelegramAuthorizationResult(
            False,
            "sender_unapproved",
            bot_id=bot_id,
            destination_id=actual_destination,
            sender_id=actual_sender,
        )
    return TelegramAuthorizationResult(
        True,
        "authorized",
        bot_id=bot_id,
        destination_id=actual_destination,
        sender_id=actual_sender,
    )


def assess_telegram_configuration(
    app_settings: Mapping[str, object] | None,
    environ: Mapping[str, object] | None,
    *,
    identity: BotIdentityVerification | None,
    approved_destination_id: object,
    destination_approved: bool,
    approved_sender_ids: Iterable[object] | None,
    now: datetime | None = None,
    max_verification_age: timedelta = DEFAULT_VERIFICATION_MAX_AGE,
) -> TelegramConfigurationAssessment:
    """Report readiness without returning credential values or changing legacy loading."""
    settings = app_settings if isinstance(app_settings, Mapping) else {}
    environment = environ if isinstance(environ, Mapping) else {}
    settings_token = settings.get("telegram_bot_token")
    environment_token = environment.get("TELEGRAM_BOT_TOKEN")
    settings_token = settings_token.strip() if isinstance(settings_token, str) else ""
    environment_token = environment_token.strip() if isinstance(environment_token, str) else ""

    raw_settings_destination = settings.get("telegram_chat_id")
    raw_environment_destination = environment.get("TELEGRAM_CHAT_ID")
    settings_destination_text = (
        raw_settings_destination.strip()
        if isinstance(raw_settings_destination, str)
        else ""
    )
    environment_destination_text = (
        raw_environment_destination.strip()
        if isinstance(raw_environment_destination, str)
        else ""
    )
    settings_destination = _normalize_destination_id(settings_destination_text)
    environment_destination = _normalize_destination_id(environment_destination_text)
    selected_destination = raw_settings_destination or raw_environment_destination
    configured_destination = _normalize_destination_id(selected_destination)

    conflicts: list[str] = []
    if settings_token and environment_token and settings_token != environment_token:
        conflicts.append("bot_token")
    if (
        settings_destination is not None
        and environment_destination is not None
        and settings_destination != environment_destination
    ):
        conflicts.append("destination")
    if (
        (settings_destination_text and settings_destination is None)
        or (environment_destination_text and environment_destination is None)
    ):
        conflicts.append("destination")
    conflicts = list(dict.fromkeys(conflicts))

    approved_destination = _normalize_destination_id(approved_destination_id)
    approved_senders = _sender_ids(approved_sender_ids)
    selected_token = environment.get("TELEGRAM_BOT_TOKEN") or settings.get(
        "telegram_bot_token"
    )
    current_time = now or datetime.now(timezone.utc)
    fresh = _verification_is_fresh(
        identity, now=current_time, max_age=max_verification_age
    )
    return TelegramConfigurationAssessment(
        bot_token_present=isinstance(selected_token, str) and bool(selected_token.strip()),
        destination_present=configured_destination is not None,
        configuration_conflicts=tuple(conflicts),
        missing_approved_destination=not (
            destination_approved is True
            and approved_destination is not None
            and configured_destination == approved_destination
        ),
        missing_sender_authorization=approved_senders is None,
        unverified_bot_identity=(
            not isinstance(identity, BotIdentityVerification) or not identity.is_verified
        ),
        stale_bot_identity=bool(
            isinstance(identity, BotIdentityVerification)
            and identity.is_verified
            and not fresh
        ),
    )


def compare_identity_configuration(
    identity: BotIdentityVerification | None,
    *,
    previous_bot_id: object,
    configured_destination_id: object,
    previously_approved_destination_id: object,
    token_rotated: bool = False,
    initial_identity_approved: bool = False,
    replacement_approved: bool = False,
    destination_change_approved: bool = False,
    now: datetime | None = None,
    max_verification_age: timedelta = DEFAULT_VERIFICATION_MAX_AGE,
) -> IdentityChangeAssessment:
    """Compare verified identity and destination without creating or changing cursors."""
    current_time = now or datetime.now(timezone.utc)
    fresh = _verification_is_fresh(
        identity, now=current_time, max_age=max_verification_age
    )
    statuses: list[str] = []
    if not isinstance(identity, BotIdentityVerification) or not identity.is_verified:
        return IdentityChangeAssessment(
            ("bot_identity_unverified",), False, False, False, token_rotated is True, False, False
        )
    if not fresh:
        return IdentityChangeAssessment(
            ("bot_identity_stale",), False, False, False, token_rotated is True, False, False
        )

    current_bot_id = _normalize_configured_bot_id(identity.bot_id)
    old_bot_id = _normalize_configured_bot_id(previous_bot_id)
    current_destination = _normalize_destination_id(configured_destination_id)
    old_destination = _normalize_destination_id(previously_approved_destination_id)
    if current_bot_id is None or current_destination is None:
        return IdentityChangeAssessment(
            ("configuration_identity_invalid",), True, False, False, token_rotated is True, False, False
        )

    initial_identity = old_bot_id is None
    bot_unchanged = not initial_identity and current_bot_id == old_bot_id
    destination_unchanged = old_destination is not None and current_destination == old_destination

    if initial_identity:
        statuses.append("initial_identity_approval_required" if initial_identity_approved is not True else "initial_identity_approved")
    elif not bot_unchanged:
        statuses.append("replacement_requires_approval" if replacement_approved is not True else "replacement_approved_new_cursor_required")
    elif token_rotated is True:
        statuses.append("token_rotation_same_bot")

    if old_destination is None or not destination_unchanged:
        statuses.append(
            "destination_change_requires_approval"
            if destination_change_approved is not True
            else "destination_change_approved_rebinding_required"
        )
    if not statuses:
        statuses.append("unchanged")

    bot_approved = (
        initial_identity_approved is True if initial_identity
        else bot_unchanged or replacement_approved is True
    )
    destination_approved = destination_unchanged or destination_change_approved is True
    cursor_reusable = bot_unchanged and destination_unchanged
    activation_eligible = bot_approved and destination_approved
    return IdentityChangeAssessment(
        tuple(statuses),
        True,
        bot_unchanged,
        destination_unchanged,
        token_rotated is True,
        cursor_reusable,
        activation_eligible,
    )
