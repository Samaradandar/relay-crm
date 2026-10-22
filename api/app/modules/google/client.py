# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Google OAuth, Calendar and Gmail behind one interface, so the rest of this module is
unit-testable without real Google credentials (the Google Cloud project isn't provisioned yet --
confirmed with Samar). `RealGoogleClient` talks to Google's plain REST endpoints directly via
httpx (already a dependency) -- no google-api-python-client/google-auth needed. `get_client()`
picks it when GOOGLE_CLIENT_ID is set, else `DevGoogleClient`, an in-memory stand-in for local
dev and tests.
"""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import get_settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
GMAIL_BASE = "https://www.googleapis.com/gmail/v1"

SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
)


@dataclass(frozen=True)
class TokenResult:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scopes: list[str]


@dataclass(frozen=True)
class UserInfo:
    email: str
    hd: str | None  # the Workspace domain, when the account belongs to one


@dataclass(frozen=True)
class GoogleEvent:
    id: str
    etag: str
    html_link: str
    hangout_link: str | None
    updated: str  # RFC3339, used for duplicate/ordering checks (section 5)
    status: str  # confirmed | cancelled
    start: str | None = None  # RFC3339, present on an incoming (from_google) event
    attendees: list[str] = field(default_factory=list)  # attendee emails, for suggestion-matching
    summary: str | None = None


@dataclass(frozen=True)
class EventPage:
    events: list[GoogleEvent]
    next_sync_token: str | None
    extended: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # event id -> extendedProperties


@dataclass(frozen=True)
class WatchResult:
    resource_id: str
    expiration: str  # epoch millis, as Google returns it


@dataclass(frozen=True)
class GmailThread:
    id: str
    subject: str
    participants: list[str]
    date: str
    snippet: str
    message_count: int


@dataclass(frozen=True)
class GmailMessage:
    id: str
    from_email: str
    to_emails: list[str]
    cc_emails: list[str]
    sent_at: str
    body_text: str
    attachments: list[dict[str, Any]]


class GoogleError(Exception):
    def __init__(self, message: str, *, status: int, invalid_grant: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.invalid_grant = invalid_grant


class GoogleClient(Protocol):
    def build_auth_url(self, state: str, code_challenge: str) -> str: ...
    async def exchange_code(self, code: str, code_verifier: str) -> TokenResult: ...
    async def refresh_access_token(self, refresh_token: str) -> TokenResult: ...
    async def revoke(self, token: str) -> None: ...
    async def get_userinfo(self, access_token: str) -> UserInfo: ...
    async def create_event(
        self, access_token: str, calendar_id: str, event_id: str, body: dict[str, Any]
    ) -> GoogleEvent: ...
    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
        etag: str | None,
    ) -> GoogleEvent: ...
    async def cancel_event(
        self, access_token: str, calendar_id: str, event_id: str, etag: str | None
    ) -> None: ...
    async def get_event(
        self, access_token: str, calendar_id: str, event_id: str
    ) -> GoogleEvent | None: ...
    async def list_events(
        self, access_token: str, calendar_id: str, sync_token: str | None, time_min: str | None
    ) -> EventPage: ...
    async def watch_calendar(
        self, access_token: str, calendar_id: str, channel_id: str, token: str, webhook_url: str
    ) -> WatchResult: ...
    async def stop_channel(self, access_token: str, channel_id: str, resource_id: str) -> None: ...
    async def search_threads(self, access_token: str, query: str) -> list[GmailThread]: ...
    async def get_thread_messages(
        self, access_token: str, thread_id: str
    ) -> list[GmailMessage]: ...


class RealGoogleClient:
    """Plain REST calls against Google's OAuth/Calendar endpoints."""

    def __init__(self) -> None:
        s = get_settings()
        self._client_id, self._client_secret = s.google_client_id, s.google_client_secret
        self._redirect_uri = s.google_redirect_uri

    def build_auth_url(self, state: str, code_challenge: str) -> str:
        params = {
            "client_id": self._client_id, "redirect_uri": self._redirect_uri,
            "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline",
            "prompt": "consent", "include_granted_scopes": "true", "state": state,
            "code_challenge": code_challenge, "code_challenge_method": "S256",
        }  # fmt: skip
        return AUTH_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())

    async def exchange_code(self, code: str, code_verifier: str) -> TokenResult:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },  # fmt: skip
            )
        return self._token_result(r)

    async def refresh_access_token(self, refresh_token: str) -> TokenResult:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                TOKEN_URL,
                data={
                    "refresh_token": refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "refresh_token",
                },  # fmt: skip
            )
        return self._token_result(r, keep_refresh=refresh_token)

    def _token_result(self, r: httpx.Response, keep_refresh: str | None = None) -> TokenResult:
        if r.status_code != 200:
            body = (
                r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            )
            raise GoogleError(
                body.get("error_description", r.text), status=r.status_code,
                invalid_grant=body.get("error") == "invalid_grant",
            )  # fmt: skip
        body = r.json()
        return TokenResult(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", keep_refresh),
            expires_in=body.get("expires_in", 3600),
            scopes=body.get("scope", "").split(),
        )

    async def revoke(self, token: str) -> None:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(REVOKE_URL, data={"token": token})

    async def get_userinfo(self, access_token: str) -> UserInfo:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        if r.status_code != 200:
            raise GoogleError(r.text, status=r.status_code)
        body = r.json()
        return UserInfo(email=body["email"], hd=body.get("hd"))

    def _headers(self, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    async def create_event(
        self, access_token: str, calendar_id: str, event_id: str, body: dict[str, Any]
    ) -> GoogleEvent:
        url = f"{CALENDAR_BASE}/calendars/{calendar_id}/events"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                url,
                json={**body, "id": event_id},
                headers=self._headers(access_token),
                params={"sendUpdates": "all" if body.pop("_send_updates", False) else "none"},
            )
        if r.status_code == 409:
            existing = await self.get_event(access_token, calendar_id, event_id)
            if existing is not None:
                return existing
        return self._event_result(r)

    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
        etag: str | None,
    ) -> GoogleEvent:
        url = f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
        headers = self._headers(access_token)
        if etag:
            headers["If-Match"] = etag
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.patch(
                url,
                json=body,
                headers=headers,
                params={"sendUpdates": "all" if body.pop("_send_updates", False) else "none"},
            )
        return self._event_result(r)

    async def cancel_event(
        self, access_token: str, calendar_id: str, event_id: str, etag: str | None
    ) -> None:
        url = f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
        headers = self._headers(access_token)
        if etag:
            headers["If-Match"] = etag
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.delete(url, headers=headers)
        if r.status_code not in (200, 204, 404, 410):
            raise GoogleError(r.text, status=r.status_code)

    async def get_event(
        self, access_token: str, calendar_id: str, event_id: str
    ) -> GoogleEvent | None:
        url = f"{CALENDAR_BASE}/calendars/{calendar_id}/events/{event_id}"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers=self._headers(access_token))
        if r.status_code == 404:
            return None
        return self._event_result(r)

    def _event_result(self, r: httpx.Response) -> GoogleEvent:
        if r.status_code not in (200, 201):
            raise GoogleError(r.text, status=r.status_code)
        body = r.json()
        return self._event_from_body(body)

    def _event_from_body(self, body: dict[str, Any]) -> GoogleEvent:
        return GoogleEvent(
            id=body["id"], etag=body.get("etag", ""), html_link=body.get("htmlLink", ""),
            hangout_link=body.get("hangoutLink"), updated=body.get("updated", ""),
            status=body.get("status", "confirmed"),
            start=(body.get("start") or {}).get("dateTime"),
            attendees=[a["email"] for a in body.get("attendees", []) if a.get("email")],
            summary=body.get("summary"),
        )  # fmt: skip

    async def list_events(
        self, access_token: str, calendar_id: str, sync_token: str | None, time_min: str | None
    ) -> EventPage:
        url = f"{CALENDAR_BASE}/calendars/{calendar_id}/events"
        params: dict[str, str] = {"singleEvents": "true"}
        if sync_token:
            params["syncToken"] = sync_token
        elif time_min:
            params["timeMin"] = time_min
        events: list[GoogleEvent] = []
        extended: dict[str, dict[str, Any]] = {}
        page_token = None
        async with httpx.AsyncClient(timeout=10) as c:
            while True:
                if page_token:
                    params["pageToken"] = page_token
                r = await c.get(url, params=params, headers=self._headers(access_token))
                if r.status_code == 410:
                    raise GoogleError("Sync token is no longer valid", status=410)
                if r.status_code != 200:
                    raise GoogleError(r.text, status=r.status_code)
                body = r.json()
                for item in body.get("items", []):
                    events.append(self._event_from_body(item))
                    extended[item["id"]] = (item.get("extendedProperties") or {}).get("private", {})
                page_token = body.get("nextPageToken")
                if not page_token:
                    return EventPage(events, body.get("nextSyncToken"), extended)

    async def watch_calendar(
        self, access_token: str, calendar_id: str, channel_id: str, token: str, webhook_url: str
    ) -> WatchResult:
        url = f"{CALENDAR_BASE}/calendars/{calendar_id}/events/watch"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                url, headers=self._headers(access_token),
                json={"id": channel_id, "type": "web_hook", "address": webhook_url, "token": token},
            )  # fmt: skip
        if r.status_code != 200:
            raise GoogleError(r.text, status=r.status_code)
        body = r.json()
        return WatchResult(resource_id=body["resourceId"], expiration=body.get("expiration", ""))

    async def stop_channel(self, access_token: str, channel_id: str, resource_id: str) -> None:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                "https://www.googleapis.com/calendar/v3/channels/stop",
                headers=self._headers(access_token),
                json={"id": channel_id, "resourceId": resource_id},
            )

    async def search_threads(self, access_token: str, query: str) -> list[GmailThread]:
        url = f"{GMAIL_BASE}/users/me/threads"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, params={"q": query}, headers=self._headers(access_token))
        if r.status_code != 200:
            raise GoogleError(r.text, status=r.status_code)
        out = []
        for item in r.json().get("threads", []):
            tr = await c.get(
                f"{url}/{item['id']}",
                params={"format": "metadata"},
                headers=self._headers(access_token),
            )
            if tr.status_code != 200:
                continue
            tb = tr.json()
            headers_of = (tb.get("messages") or [{}])[0].get("payload", {}).get("headers", [])
            h = {x["name"]: x["value"] for x in headers_of}
            out.append(
                GmailThread(
                    id=item["id"],
                    subject=h.get("Subject", ""),
                    participants=[h.get("From", "")],
                    date=h.get("Date", ""),
                    snippet=tb.get("snippet", ""),
                    message_count=len(tb.get("messages", [])),
                )
            )
        return out

    async def get_thread_messages(self, access_token: str, thread_id: str) -> list[GmailMessage]:
        url = f"{GMAIL_BASE}/users/me/threads/{thread_id}"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, params={"format": "full"}, headers=self._headers(access_token))
        if r.status_code != 200:
            raise GoogleError(r.text, status=r.status_code)
        out = []
        for m in r.json().get("messages", []):
            headers_of = {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}
            out.append(
                GmailMessage(
                    id=m["id"],
                    from_email=headers_of.get("From", ""),
                    to_emails=[e.strip() for e in headers_of.get("To", "").split(",") if e.strip()],
                    cc_emails=[e.strip() for e in headers_of.get("Cc", "").split(",") if e.strip()],
                    sent_at=headers_of.get("Date", ""),
                    body_text=m.get("snippet", ""),
                    attachments=[],
                )
            )
        return out


@dataclass
class DevGoogleClient:
    """In-memory stand-in: no network, deterministic, used whenever GOOGLE_CLIENT_ID is unset
    (local dev and every unit test). Lets ST-01/02's logic be fully exercised today."""

    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    accounts: dict[str, UserInfo] = field(default_factory=dict)
    # Test hooks: pre-load these to simulate Google's own state for incremental sync/Gmail.
    incoming_changes: list[dict[str, Any]] = field(default_factory=list)  # events.list results
    sync_tokens_seen: list[str | None] = field(default_factory=list)
    invalid_sync_token: str | None = None  # set to a token value to force a 410 on that token
    gmail_threads: dict[str, GmailThread] = field(default_factory=dict)
    gmail_messages: dict[str, list[GmailMessage]] = field(default_factory=dict)
    stopped_channels: list[str] = field(default_factory=list)

    def build_auth_url(self, state: str, code_challenge: str) -> str:
        return f"https://accounts.google.com/dev-stub?state={state}"

    async def exchange_code(self, code: str, code_verifier: str) -> TokenResult:
        return TokenResult(
            access_token=f"dev-access-{code}", refresh_token=f"dev-refresh-{code}",
            expires_in=3600, scopes=list(SCOPES),
        )  # fmt: skip

    async def refresh_access_token(self, refresh_token: str) -> TokenResult:
        if refresh_token in self.revoked:
            raise GoogleError("Token has been revoked", status=400, invalid_grant=True)
        return TokenResult(
            access_token=f"dev-access-{refresh_token}", refresh_token=refresh_token,
            expires_in=3600, scopes=list(SCOPES),
        )  # fmt: skip

    async def revoke(self, token: str) -> None:
        self.revoked.append(token)

    async def get_userinfo(self, access_token: str) -> UserInfo:
        return self.accounts.get(access_token, UserInfo(email="dev@example.com", hd="example.com"))

    async def create_event(
        self, access_token: str, calendar_id: str, event_id: str, body: dict[str, Any]
    ) -> GoogleEvent:
        if event_id in self.events:  # the deterministic-id retry case (section 4)
            return self._to_event(event_id)
        self.events[event_id] = {**body, "status": "confirmed", "etag": uuid.uuid4().hex}
        return self._to_event(event_id)

    async def update_event(
        self,
        access_token: str,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
        etag: str | None,
    ) -> GoogleEvent:
        existing = self.events.get(event_id)
        if existing is None:
            raise GoogleError("Event not found", status=404)
        if etag and existing.get("etag") != etag:
            raise GoogleError("Precondition Failed", status=412)
        existing.update(body)
        existing["etag"] = uuid.uuid4().hex
        return self._to_event(event_id)

    async def cancel_event(
        self, access_token: str, calendar_id: str, event_id: str, etag: str | None
    ) -> None:
        existing = self.events.get(event_id)
        if existing is not None:
            existing["status"] = "cancelled"

    async def get_event(
        self, access_token: str, calendar_id: str, event_id: str
    ) -> GoogleEvent | None:
        if event_id not in self.events:
            return None
        return self._to_event(event_id)

    async def list_events(
        self, access_token: str, calendar_id: str, sync_token: str | None, time_min: str | None
    ) -> EventPage:
        self.sync_tokens_seen.append(sync_token)
        if sync_token and sync_token == self.invalid_sync_token:
            raise GoogleError("Sync token is no longer valid", status=410)
        changes, extended = [], {}
        for change in self.incoming_changes:
            changes.append(self._event_from_dict(change))
            extended[change["id"]] = change.get("extendedProperties", {}).get("private", {})
        self.incoming_changes = []  # each poll drains what's "arrived" since the last one
        return EventPage(
            changes, next_sync_token=f"token-{uuid.uuid4().hex[:8]}", extended=extended
        )

    def _event_from_dict(self, d: dict[str, Any]) -> GoogleEvent:
        return GoogleEvent(
            id=d["id"], etag=d.get("etag", uuid.uuid4().hex), html_link=d.get("htmlLink", ""),
            hangout_link=d.get("hangoutLink"), updated=d.get("updated", str(time.time())),
            status=d.get("status", "confirmed"), start=(d.get("start") or {}).get("dateTime"),
            attendees=d.get("attendees", []), summary=d.get("summary"),
        )  # fmt: skip

    async def watch_calendar(
        self, access_token: str, calendar_id: str, channel_id: str, token: str, webhook_url: str
    ) -> WatchResult:
        return WatchResult(
            resource_id=f"res-{channel_id}", expiration=str(int(time.time() + 86400) * 1000)
        )

    async def stop_channel(self, access_token: str, channel_id: str, resource_id: str) -> None:
        self.stopped_channels.append(channel_id)

    async def search_threads(self, access_token: str, query: str) -> list[GmailThread]:
        return list(self.gmail_threads.values())

    async def get_thread_messages(self, access_token: str, thread_id: str) -> list[GmailMessage]:
        return self.gmail_messages.get(thread_id, [])

    def _to_event(self, event_id: str) -> GoogleEvent:
        body = self.events[event_id]
        return GoogleEvent(
            id=event_id, etag=body["etag"], html_link=f"https://calendar.google.com/event?eid={event_id}",
            hangout_link="https://meet.google.com/dev-stub" if body.get("_meet") else None,
            updated=str(time.time()), status=body.get("status", "confirmed"),
            start=(body.get("start") or {}).get("dateTime"),
        )  # fmt: skip


_dev_client: DevGoogleClient | None = None


def get_client() -> GoogleClient:
    settings = get_settings()
    if settings.google_client_id:
        return RealGoogleClient()
    global _dev_client
    if _dev_client is None:
        _dev_client = DevGoogleClient()
    return _dev_client


def reset_dev_client() -> None:
    """Test isolation: the dev client is a process-wide singleton (it stands in for Google's own
    server state, which is also process-wide from the app's point of view) -- tests that check
    its event store call this first so one test's events don't leak into another's."""
    global _dev_client
    _dev_client = None
