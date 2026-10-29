# -*- coding: utf-8 -*-
"""Minimal server-side Supabase Auth helper for Streamlit.

Tokens are held only in Streamlit's server-side session_state. This module
validates the access token with Supabase Auth; it does not treat a decoded JWT
as proof of identity.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import re
import time
from typing import Any

import requests

MIN_PASSWORD_LENGTH = 8
SESSION_KEY = "_stockflow_auth_session"
IDLE_TIMEOUT_SECONDS = 60 * 60
MAX_SESSION_SECONDS = 12 * 60 * 60
REVALIDATE_SECONDS = 5 * 60
REFRESH_BEFORE_SECONDS = 120
HTTP_TIMEOUT = 12


@dataclass(frozen=True)
class AuthConfig:
    url: str
    anon_key: str


class AuthError(RuntimeError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def build_config(url: str, anon_key: str) -> AuthConfig:
    url = (url or "").strip().rstrip("/")
    anon_key = (anon_key or "").strip()
    if not url or not anon_key:
        raise AuthError("SUPABASE_URL ou SUPABASE_ANON_KEY manquant dans les Secrets.")
    if not re.match(r"^https://[A-Za-z0-9.-]+(?::\d+)?$", url):
        raise AuthError("SUPABASE_URL invalide. Utilisez l'URL HTTPS de votre projet Supabase.")
    # Block modern secret keys by prefix and legacy service_role JWTs by their
    # decoded role claim. Decoding here is only a key-type safety check; it is
    # never used to authenticate a user or validate an access token.
    if anon_key.startswith("sb_secret_") or "service_role" in anon_key.lower():
        raise AuthError("N'utilisez jamais une clé service_role ou sb_secret dans cette application.")
    parts = anon_key.split(".")
    if len(parts) == 3:
        try:
            payload = parts[1] + ("=" * (-len(parts[1]) % 4))
            claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
            if str(claims.get("role", "")).lower() == "service_role":
                raise AuthError("La clé fournie est une service_role. Utilisez la clé publique anon/publishable.")
        except AuthError:
            raise
        except (ValueError, UnicodeError, json.JSONDecodeError):
            # Do not reject unknown public key formats solely because they are
            # not JWTs; Supabase will validate the key when a request is made.
            pass
    return AuthConfig(url=url, anon_key=anon_key)


def _headers(cfg: AuthConfig, token: str | None = None) -> dict[str, str]:
    h = {"apikey": cfg.anon_key, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _request(cfg: AuthConfig, method: str, path: str, *, token: str | None = None,
             payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = requests.request(
            method, cfg.url + path, headers=_headers(cfg, token), json=payload,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AuthError("Impossible de contacter Supabase. Vérifiez votre connexion et réessayez.") from exc
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if not response.ok:
        raw = str(data.get("msg") or data.get("message") or data.get("error_description") or "")
        low = raw.lower()
        if response.status_code in (400, 401, 403):
            if "email not confirmed" in low:
                message = "Confirmez votre adresse email depuis le lien envoyé par Supabase."
            elif "invalid login credentials" in low:
                message = "Email ou mot de passe incorrect."
            elif "already registered" in low or "user already registered" in low:
                message = "Impossible de créer ce compte avec ces informations. Essayez de vous connecter."
            elif "password" in low and ("short" in low or "least" in low):
                message = f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
            else:
                message = "Demande refusée par Supabase. Vérifiez les informations puis réessayez."
        elif response.status_code == 429:
            message = "Trop de tentatives. Attendez un peu avant de réessayer."
        else:
            message = "Service d'authentification temporairement indisponible. Réessayez plus tard."
        raise AuthError(message)
    return data if isinstance(data, dict) else {}


def _normalise_session(data: dict[str, Any]) -> dict[str, Any] | None:
    user = data.get("user") or {}
    access = data.get("access_token")
    if not access or not user.get("id"):
        return None
    now = time.time()
    expires_in = int(data.get("expires_in") or 3600)
    return {
        "access_token": str(access),
        "refresh_token": str(data.get("refresh_token") or ""),
        "expires_at": now + expires_in,
        "user_id": str(user["id"]),
        "email": str(user.get("email") or ""),
        "created_at": now,
        "last_activity": now,
        "last_validated": 0.0,
    }


def sign_in(cfg: AuthConfig, email: str, password: str) -> dict[str, Any]:
    email = (email or "").strip().lower()
    if not email or not password:
        raise AuthError("Entrez votre email et votre mot de passe.")
    data = _request(cfg, "POST", "/auth/v1/token?grant_type=password",
                    payload={"email": email, "password": password})
    session = _normalise_session(data)
    if not session:
        raise AuthError("Connexion impossible. Vérifiez les informations et réessayez.")
    return session


def sign_up(cfg: AuthConfig, email: str, password: str) -> dict[str, Any] | None:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise AuthError("Entrez une adresse email valide.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.")
    data = _request(cfg, "POST", "/auth/v1/signup", payload={"email": email, "password": password})
    return _normalise_session(data)


def start_session(state: Any, session: dict[str, Any]) -> None:
    # Copy only expected fields to avoid retaining unexpected response content.
    state[SESSION_KEY] = {k: session[k] for k in (
        "access_token", "refresh_token", "expires_at", "user_id", "email",
        "created_at", "last_activity", "last_validated") if k in session}


def current_user_id(state: Any) -> str:
    session = state.get(SESSION_KEY) or {}
    uid = session.get("user_id")
    if not uid:
        raise PermissionError("Aucune session authentifiée.")
    return str(uid)


def _refresh(cfg: AuthConfig, session: dict[str, Any]) -> dict[str, Any] | None:
    refresh_token = session.get("refresh_token")
    if not refresh_token:
        return None
    data = _request(cfg, "POST", "/auth/v1/token?grant_type=refresh_token",
                    payload={"refresh_token": refresh_token})
    new_session = _normalise_session(data)
    if new_session:
        # Preserve original session-age boundary.
        new_session["created_at"] = session.get("created_at", time.time())
    return new_session


def ensure_session(cfg: AuthConfig, state: Any) -> tuple[dict[str, Any] | None, str | None]:
    session = state.get(SESSION_KEY)
    if not session:
        return None, None
    now = time.time()
    if now - float(session.get("last_activity", now)) > IDLE_TIMEOUT_SECONDS:
        state.pop(SESSION_KEY, None)
        return None, "Session expirée après une période d'inactivité. Reconnectez-vous."
    if now - float(session.get("created_at", now)) > MAX_SESSION_SECONDS:
        state.pop(SESSION_KEY, None)
        return None, "La durée maximale de session est atteinte. Reconnectez-vous."

    if now >= float(session.get("expires_at", 0)) - REFRESH_BEFORE_SECONDS:
        try:
            refreshed = _refresh(cfg, session)
        except AuthError:
            # If token already expired, fail closed; otherwise allow temporary outage only
            # until the access token's recorded expiry.
            if now >= float(session.get("expires_at", 0)):
                state.pop(SESSION_KEY, None)
                return None, "Session expirée. Reconnectez-vous."
            refreshed = None
        if refreshed:
            if refreshed.get("user_id") != session.get("user_id"):
                state.pop(SESSION_KEY, None)
                return None, "Identité de session invalide. Reconnectez-vous."
            # Retain activity/session start and replace refreshed credentials.
            refreshed["created_at"] = session.get("created_at", now)
            refreshed["last_activity"] = now
            start_session(state, refreshed)
            session = state[SESSION_KEY]

    if now - float(session.get("last_validated", 0)) >= REVALIDATE_SECONDS:
        try:
            user = _request(cfg, "GET", "/auth/v1/user", token=session.get("access_token"))
        except AuthError:
            if now >= float(session.get("expires_at", 0)):
                state.pop(SESSION_KEY, None)
                return None, "Session expirée ou invalide. Reconnectez-vous."
            # A transient outage does not prove the token is invalid; database access
            # must still independently fail if downstream services are unavailable.
            session["last_activity"] = now
            return {"id": session["user_id"], "email": session.get("email", "")}, ""
        if str(user.get("id") or "") != str(session.get("user_id") or ""):
            state.pop(SESSION_KEY, None)
            return None, "Identité de session invalide. Reconnectez-vous."
        session["last_validated"] = now
        session["last_activity"] = now
    else:
        session["last_activity"] = now
    return {"id": str(session["user_id"]), "email": session.get("email", "")}, None


def sign_out(cfg: AuthConfig, token: str | None) -> None:
    if not token:
        return
    try:
        _request(cfg, "POST", "/auth/v1/logout", token=token)
    except AuthError:
        # Local session is cleared by caller regardless of remote logout availability.
        pass
