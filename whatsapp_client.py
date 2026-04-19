import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("whatsapp_client")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WhatsAppClientError(Exception):
    """Base exception for WhatsApp client errors."""


class ConnectionError(WhatsAppClientError):
    """Raised when a connection-related operation fails."""


class SessionError(WhatsAppClientError):
    """Raised for session management problems."""


class APIError(WhatsAppClientError):
    """Raised when the Baileys bridge returns a non-2xx response."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status}: {message}")


class RateLimitError(WhatsAppClientError):
    """Raised when rate limiting prevents the operation."""


# ---------------------------------------------------------------------------
# NodeBaileysBridge — API contract documentation
# ---------------------------------------------------------------------------


class NodeBaileysBridge:
    """
    Documents the REST API contract expected from the Node.js Baileys server.

    The Node server should be implemented using @whiskeysockets/baileys and
    expose the following endpoints on a configurable port (default: 3000).

    All request/response bodies are JSON unless otherwise noted.
    All endpoints that accept a session_id expect it as a path parameter:
        /sessions/:session_id/...

    ── CONNECTION ─────────────────────────────────────────────────────────────

    POST /sessions/:session_id/connect/qr
        Response: { qr_code: "<base64-png>", expires_in: <seconds> }

    POST /sessions/:session_id/connect/phone
        Body:    { phone_number: "<E.164 format>" }
        Response: { otp_requested: true, message: "OTP sent via WhatsApp" }

    POST /sessions/:session_id/connect/verify-otp
        Body:    { otp: "<6-digit code>" }
        Response: { connected: true, phone: "<number>" }

    GET  /sessions/:session_id/status
        Response: { connected: <bool>, phone: "<number>", uptime: <seconds> }

    DELETE /sessions/:session_id
        Response: { disconnected: true }

    ── GROUPS ─────────────────────────────────────────────────────────────────

    POST /sessions/:session_id/groups
        Body:    { name: "<string>", participants: ["<phone>", ...] }
        Response: { group_jid: "<jid>", name: "<string>", created_at: "<iso8601>" }

    POST /sessions/:session_id/groups/join
        Body:    { invite_code: "<code>" }
        Response: { group_jid: "<jid>", name: "<string>" }

    DELETE /sessions/:session_id/groups/:group_jid/leave
        Response: { left: true }

    GET  /sessions/:session_id/groups
        Response: [ { group_jid, name, participant_count, is_admin }, ... ]

    GET  /sessions/:session_id/groups/:group_jid/invite-link
        Response: { invite_link: "<url>" }

    GET  /sessions/:session_id/groups/:group_jid/metadata
        Response: { group_jid, name, description, participants: [...], admins: [...],
                    created_at, owner_jid }

    ── GROUP SETTINGS ─────────────────────────────────────────────────────────

    PATCH /sessions/:session_id/groups/:group_jid/subject
        Body:    { subject: "<string>" }
        Response: { updated: true }

    PUT   /sessions/:session_id/groups/:group_jid/photo
        Body:    multipart/form-data  { image: <file> }
        Response: { updated: true, photo_url: "<url>" }

    PATCH /sessions/:session_id/groups/:group_jid/settings
        Body:    { restrict?: <bool>, announce?: <bool>, approval?: <bool>, ... }
        Response: { updated: true, settings: { ... } }

    PATCH /sessions/:session_id/groups/:group_jid/disappearing-messages
        Body:    { duration: <seconds|0 to disable> }
        Response: { updated: true, duration: <seconds> }

    ── MEMBERS ────────────────────────────────────────────────────────────────

    POST   /sessions/:session_id/groups/:group_jid/members
        Body:    { phone: "<E.164>" }
        Response: { added: true, participant_jid: "<jid>" }

    DELETE /sessions/:session_id/groups/:group_jid/members/:phone
        Response: { removed: true }

    PATCH  /sessions/:session_id/groups/:group_jid/members/:phone/promote
        Response: { promoted: true }

    PATCH  /sessions/:session_id/groups/:group_jid/members/:phone/demote
        Response: { demoted: true }

    GET    /sessions/:session_id/groups/:group_jid/pending-requests
        Response: [ { phone, requested_at }, ... ]

    POST   /sessions/:session_id/groups/:group_jid/pending-requests/:phone/approve
        Response: { approved: true }

    POST   /sessions/:session_id/groups/:group_jid/pending-requests/:phone/reject
        Response: { rejected: true }

    ── ERROR RESPONSES ────────────────────────────────────────────────────────

    All errors follow:
        { error: "<message>", code: "<ERROR_CODE>", details?: { ... } }

    Common HTTP status codes:
        400 Bad Request     — invalid parameters
        401 Unauthorized    — session not authenticated
        404 Not Found       — session or group does not exist
        409 Conflict        — duplicate resource (e.g., already in group)
        429 Too Many Requests — rate limited by WhatsApp
        500 Internal Error  — Baileys / Node.js error
    """

    # This class is documentation-only; no runtime behaviour.
    pass


# ---------------------------------------------------------------------------
# WhatsAppClient
# ---------------------------------------------------------------------------


class WhatsAppClient:
    """
    Async HTTP client for the Node.js Baileys bridge.

    Parameters
    ----------
    base_url : str
        Base URL of the Baileys Node.js server (default: http://localhost:3000).
    rate_limit_delay : float
        Minimum seconds to wait between successive API calls (default: 2.0).
    max_retries : int
        Maximum number of retry attempts for transient failures (default: 3).
    retry_base_delay : float
        Base delay (seconds) for exponential back-off on retries (default: 1.0).
    request_timeout : float
        Total timeout (seconds) for a single HTTP request (default: 30.0).

    Usage
    -----
    Preferred — use as an async context manager so the aiohttp session is
    cleaned up automatically::

        async with WhatsAppClient() as client:
            qr = await client.connect_via_qr("my-session")

    Manual lifecycle::

        client = WhatsAppClient()
        await client.start()
        # … use client …
        await client.stop()
    """

    def __init__(
        self,
        base_url: str = "http://localhost:3000",
        rate_limit_delay: float = 2.0,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        request_timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.request_timeout = aiohttp.ClientTimeout(total=request_timeout)

        self._session: Optional[aiohttp.ClientSession] = None
        self._last_call_time: float = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialise the underlying aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.request_timeout,
                headers={"Content-Type": "application/json"},
            )
            logger.debug("aiohttp session created (base_url=%s)", self.base_url)

    async def stop(self) -> None:
        """Close the underlying aiohttp ClientSession."""
        if self._session and not self._session.closed:
            await self._session.close()
            logger.debug("aiohttp session closed")

    async def __aenter__(self) -> "WhatsAppClient":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _enforce_rate_limit(self) -> None:
        """Sleep if necessary to honour the configured rate-limit delay."""
        elapsed = time.monotonic() - self._last_call_time
        if elapsed < self.rate_limit_delay:
            sleep_for = self.rate_limit_delay - elapsed
            logger.debug("Rate-limit: sleeping %.2fs", sleep_for)
            await asyncio.sleep(sleep_for)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        data: Optional[aiohttp.FormData] = None,
        headers: Optional[dict] = None,
    ) -> Any:
        """
        Execute an HTTP request with retry / back-off logic.

        Parameters
        ----------
        method : str
            HTTP verb (GET, POST, PATCH, DELETE, PUT).
        path : str
            URL path relative to base_url (must start with '/').
        json : dict, optional
            JSON body for the request.
        data : aiohttp.FormData, optional
            Multipart form data (mutually exclusive with json).
        headers : dict, optional
            Extra HTTP headers to merge.

        Returns
        -------
        Any
            Parsed JSON response body.

        Raises
        ------
        APIError
            On non-2xx responses after all retries are exhausted.
        WhatsAppClientError
            On network-level errors after all retries are exhausted.
        """
        if self._session is None or self._session.closed:
            raise SessionError("Client not started — call start() or use 'async with'.")

        url = f"{self.base_url}{path}"
        attempt = 0
        last_exc: Exception = RuntimeError("Unknown error")

        while attempt <= self.max_retries:
            await self._enforce_rate_limit()

            try:
                logger.debug("→ %s %s (attempt %d)", method.upper(), url, attempt + 1)
                req_kwargs: dict[str, Any] = {"headers": headers or {}}
                if data is not None:
                    req_kwargs["data"] = data
                    # Remove default Content-Type so aiohttp sets multipart boundary
                    req_kwargs["headers"]["Content-Type"] = ""
                elif json is not None:
                    req_kwargs["json"] = json

                async with self._session.request(
                    method.upper(), url, **req_kwargs
                ) as resp:
                    self._last_call_time = time.monotonic()
                    body = await resp.json(content_type=None)

                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", self.retry_base_delay * (2 ** attempt)))
                        logger.warning("Rate-limited by server; waiting %.1fs", retry_after)
                        await asyncio.sleep(retry_after)
                        attempt += 1
                        last_exc = RateLimitError("Server-side rate limit hit.")
                        continue

                    if not (200 <= resp.status < 300):
                        msg = body.get("error", "Unknown API error") if isinstance(body, dict) else str(body)
                        raise APIError(resp.status, msg)

                    logger.debug("← %d %s", resp.status, url)
                    return body

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    logger.error("Request failed after %d attempts: %s", attempt + 1, exc)
                    raise WhatsAppClientError(f"Network error after {attempt + 1} attempts: {exc}") from exc

                backoff = self.retry_base_delay * (2 ** attempt)
                logger.warning("Transient error (%s); retrying in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                attempt += 1

            except APIError:
                # Non-retryable (e.g. 400, 401, 404) — propagate immediately
                raise

        raise WhatsAppClientError(f"Exhausted {self.max_retries} retries.") from last_exc

    # ── CONNECTION ─────────────────────────────────────────────────────────

    async def connect_via_qr(self, session_id: str) -> dict:
        """
        Initiate a QR-code-based WhatsApp Web login for the given session.

        The Node.js server generates a QR code via Baileys and returns it as
        base64-encoded PNG data. Display it to the user so they can scan it
        with the WhatsApp mobile app.

        Parameters
        ----------
        session_id : str
            Unique identifier for this WhatsApp session (e.g. "user-42").

        Returns
        -------
        dict
            {
                "qr_code": "<base64-png string>",
                "expires_in": <int seconds>
            }

        Raises
        ------
        ConnectionError
            If the server cannot generate a QR code.
        """
        logger.info("[%s] Requesting QR code login", session_id)
        try:
            result = await self._request("POST", f"/sessions/{session_id}/connect/qr")
            logger.info("[%s] QR code received (expires in %ss)", session_id, result.get("expires_in"))
            return result
        except APIError as exc:
            raise ConnectionError(f"QR connect failed for session '{session_id}': {exc}") from exc

    async def connect_via_phone(self, session_id: str, phone_number: str) -> dict:
        """
        Initiate phone-number-based pairing for the given session.

        The Node.js bridge instructs Baileys to request a pairing OTP from
        WhatsApp for the supplied phone number.

        Parameters
        ----------
        session_id : str
            Unique identifier for this WhatsApp session.
        phone_number : str
            Phone number in E.164 format (e.g. "+14155552671").

        Returns
        -------
        dict
            {
                "otp_requested": true,
                "message": "OTP sent via WhatsApp"
            }

        Raises
        ------
        ConnectionError
            If the OTP request fails.
        """
        logger.info("[%s] Requesting phone-number pairing for %s", session_id, phone_number)
        try:
            result = await self._request(
                "POST",
                f"/sessions/{session_id}/connect/phone",
                json={"phone_number": phone_number},
            )
            logger.info("[%s] OTP requested successfully", session_id)
            return result
        except APIError as exc:
            raise ConnectionError(f"Phone connect failed for session '{session_id}': {exc}") from exc

    async def verify_otp(self, session_id: str, otp: str) -> dict:
        """
        Submit the OTP received via WhatsApp to complete phone-number pairing.

        Parameters
        ----------
        session_id : str
            Unique identifier for this WhatsApp session.
        otp : str
            The 6-digit OTP delivered by WhatsApp.

        Returns
        -------
        dict
            {
                "connected": true,
                "phone": "<verified phone number>"
            }

        Raises
        ------
        ConnectionError
            If OTP verification fails (wrong code, expired, etc.).
        """
        logger.info("[%s] Verifying OTP", session_id)
        try:
            result = await self._request(
                "POST",
                f"/sessions/{session_id}/connect/verify-otp",
                json={"otp": otp},
            )
            logger.info("[%s] OTP verified — connected as %s", session_id, result.get("phone"))
            return result
        except APIError as exc:
            raise ConnectionError(f"OTP verification failed for session '{session_id}': {exc}") from exc

    async def get_connection_status(self, session_id: str) -> dict:
        """
        Poll the connection status of a session.

        Parameters
        ----------
        session_id : str
            Unique identifier for this WhatsApp session.

        Returns
        -------
        dict
            {
                "connected": <bool>,
                "phone": "<number or null>",
                "uptime": <seconds since connection>
            }

        Raises
        ------
        SessionError
            If the session does not exist on the server.
        """
        logger.debug("[%s] Checking connection status", session_id)
        try:
            return await self._request("GET", f"/sessions/{session_id}/status")
        except APIError as exc:
            raise SessionError(f"Could not fetch status for session '{session_id}': {exc}") from exc

    async def disconnect(self, session_id: str) -> dict:
        """
        Disconnect and destroy a session on the Node.js server.

        After calling this method the session_id is no longer valid; a new
        connect_via_qr / connect_via_phone flow must be started to reconnect.

        Parameters
        ----------
        session_id : str
            Unique identifier for the session to disconnect.

        Returns
        -------
        dict
            { "disconnected": true }

        Raises
        ------
        SessionError
            If the session does not exist or cannot be disconnected.
        """
        logger.info("[%s] Disconnecting session", session_id)
        try:
            result = await self._request("DELETE", f"/sessions/{session_id}")
            logger.info("[%s] Session disconnected", session_id)
            return result
        except APIError as exc:
            raise SessionError(f"Disconnect failed for session '{session_id}': {exc}") from exc

    # ── GROUPS ─────────────────────────────────────────────────────────────

    async def create_group(
        self,
        session_id: str,
        name: str,
        participants: Optional[list] = None,
    ) -> dict:
        """
        Create a new WhatsApp group.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        name : str
            Display name for the new group.
        participants : list of str, optional
            Phone numbers (E.164) to add immediately. The session owner is
            added automatically by WhatsApp.

        Returns
        -------
        dict
            {
                "group_jid": "<jid>",
                "name": "<string>",
                "created_at": "<iso8601>"
            }

        Raises
        ------
        APIError
            If group creation fails (e.g. invalid participants).
        """
        logger.info("[%s] Creating group '%s' with %d participant(s)", session_id, name, len(participants or []))
        return await self._request(
            "POST",
            f"/sessions/{session_id}/groups",
            json={"name": name, "participants": participants or []},
        )

    async def join_group(self, session_id: str, invite_code: str) -> dict:
        """
        Join a WhatsApp group via an invite link code.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        invite_code : str
            The alphanumeric code from a WhatsApp invite URL
            (e.g. "ABC123" from "https://chat.whatsapp.com/ABC123").

        Returns
        -------
        dict
            { "group_jid": "<jid>", "name": "<string>" }

        Raises
        ------
        APIError
            If the code is invalid, expired, or the group is full.
        """
        logger.info("[%s] Joining group via invite code '%s'", session_id, invite_code)
        return await self._request(
            "POST",
            f"/sessions/{session_id}/groups/join",
            json={"invite_code": invite_code},
        )

    async def leave_group(self, session_id: str, group_jid: str) -> dict:
        """
        Leave a WhatsApp group.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group (e.g. "120363000000000001@g.us").

        Returns
        -------
        dict
            { "left": true }

        Raises
        ------
        APIError
            If the group does not exist or the session is not a member.
        """
        logger.info("[%s] Leaving group %s", session_id, group_jid)
        return await self._request(
            "DELETE",
            f"/sessions/{session_id}/groups/{group_jid}/leave",
        )

    async def get_all_groups(self, session_id: str) -> list:
        """
        Retrieve all groups that the session's WhatsApp account belongs to.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.

        Returns
        -------
        list of dict
            Each entry: { group_jid, name, participant_count, is_admin }

        Raises
        ------
        SessionError
            If the session is not connected.
        """
        logger.info("[%s] Fetching all groups", session_id)
        result = await self._request("GET", f"/sessions/{session_id}/groups")
        logger.info("[%s] Found %d group(s)", session_id, len(result))
        return result

    async def get_group_invite_link(self, session_id: str, group_jid: str) -> str:
        """
        Retrieve the invite link for a group.

        Requires the session to be a group admin.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.

        Returns
        -------
        str
            Full invite URL (e.g. "https://chat.whatsapp.com/ABC123").

        Raises
        ------
        APIError
            If the session is not an admin or the group is not found.
        """
        logger.info("[%s] Getting invite link for group %s", session_id, group_jid)
        result = await self._request(
            "GET",
            f"/sessions/{session_id}/groups/{group_jid}/invite-link",
        )
        return result["invite_link"]

    async def get_group_metadata(self, session_id: str, group_jid: str) -> dict:
        """
        Fetch detailed metadata for a group.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.

        Returns
        -------
        dict
            {
                "group_jid": str,
                "name": str,
                "description": str,
                "owner_jid": str,
                "created_at": str,
                "participants": [{"jid": str, "phone": str, "is_admin": bool}, ...],
                "admins": [str]  # list of admin JIDs
            }

        Raises
        ------
        APIError
            If the group does not exist.
        """
        logger.info("[%s] Fetching metadata for group %s", session_id, group_jid)
        return await self._request(
            "GET",
            f"/sessions/{session_id}/groups/{group_jid}/metadata",
        )

    # ── GROUP SETTINGS ─────────────────────────────────────────────────────

    async def update_group_subject(
        self, session_id: str, group_jid: str, subject: str
    ) -> dict:
        """
        Update the display name (subject) of a group.

        Requires admin privileges.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        subject : str
            New group name (max 25 characters enforced by WhatsApp).

        Returns
        -------
        dict
            { "updated": true }

        Raises
        ------
        APIError
            If the session lacks admin rights or the subject is invalid.
        """
        logger.info("[%s] Updating subject of group %s to '%s'", session_id, group_jid, subject)
        return await self._request(
            "PATCH",
            f"/sessions/{session_id}/groups/{group_jid}/subject",
            json={"subject": subject},
        )

    async def update_group_photo(
        self, session_id: str, group_jid: str, image_path: str
    ) -> dict:
        """
        Update the profile photo of a group.

        Requires admin privileges. The image is uploaded as multipart/form-data.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        image_path : str
            Local filesystem path to a JPEG or PNG image file.

        Returns
        -------
        dict
            { "updated": true, "photo_url": "<url>" }

        Raises
        ------
        FileNotFoundError
            If image_path does not exist locally.
        APIError
            If the upload fails or the session lacks admin rights.
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        logger.info("[%s] Uploading group photo for %s from '%s'", session_id, group_jid, image_path)

        form = aiohttp.FormData()
        form.add_field(
            "image",
            path.read_bytes(),
            filename=path.name,
            content_type="image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png",
        )
        return await self._request(
            "PUT",
            f"/sessions/{session_id}/groups/{group_jid}/photo",
            data=form,
        )

    async def update_group_settings(
        self, session_id: str, group_jid: str, settings: dict
    ) -> dict:
        """
        Update group settings such as messaging restrictions and approval mode.

        Requires admin privileges.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        settings : dict
            Key-value pairs of settings to change. Supported keys:
              - restrict (bool): Only admins can send messages.
              - announce (bool): Only admins can change group info.
              - approval (bool): New members require admin approval.

        Returns
        -------
        dict
            { "updated": true, "settings": { <applied settings> } }

        Raises
        ------
        APIError
            If any setting key is invalid or the session lacks admin rights.
        """
        logger.info("[%s] Updating settings for group %s: %s", session_id, group_jid, settings)
        return await self._request(
            "PATCH",
            f"/sessions/{session_id}/groups/{group_jid}/settings",
            json=settings,
        )

    async def set_disappearing_messages(
        self, session_id: str, group_jid: str, duration: int
    ) -> dict:
        """
        Enable or disable disappearing messages in a group.

        Requires admin privileges.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        duration : int
            Disappearing-message timer in seconds.
            Common values: 86400 (24 h), 604800 (7 days), 7776000 (90 days).
            Pass 0 to disable disappearing messages.

        Returns
        -------
        dict
            { "updated": true, "duration": <seconds> }

        Raises
        ------
        APIError
            If the duration is invalid or the session lacks admin rights.
        """
        logger.info(
            "[%s] Setting disappearing messages for group %s to %ds",
            session_id, group_jid, duration,
        )
        return await self._request(
            "PATCH",
            f"/sessions/{session_id}/groups/{group_jid}/disappearing-messages",
            json={"duration": duration},
        )

    # ── MEMBERS ────────────────────────────────────────────────────────────

    async def add_member(
        self, session_id: str, group_jid: str, phone: str
    ) -> dict:
        """
        Add a participant to a group.

        Requires admin privileges. The phone number must be a registered
        WhatsApp user.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        phone : str
            Phone number in E.164 format of the user to add.

        Returns
        -------
        dict
            { "added": true, "participant_jid": "<jid>" }

        Raises
        ------
        APIError
            If the phone number is not on WhatsApp, already in the group,
            or the session lacks admin rights.
        """
        logger.info("[%s] Adding %s to group %s", session_id, phone, group_jid)
        return await self._request(
            "POST",
            f"/sessions/{session_id}/groups/{group_jid}/members",
            json={"phone": phone},
        )

    async def remove_member(
        self, session_id: str, group_jid: str, phone: str
    ) -> dict:
        """
        Remove a participant from a group.

        Requires admin privileges.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        phone : str
            Phone number in E.164 format of the user to remove.

        Returns
        -------
        dict
            { "removed": true }

        Raises
        ------
        APIError
            If the user is not in the group or the session lacks admin rights.
        """
        logger.info("[%s] Removing %s from group %s", session_id, phone, group_jid)
        return await self._request(
            "DELETE",
            f"/sessions/{session_id}/groups/{group_jid}/members/{phone}",
        )

    async def make_admin(
        self, session_id: str, group_jid: str, phone: str
    ) -> dict:
        """
        Promote a group member to admin.

        Requires the session to be an admin.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        phone : str
            Phone number in E.164 format of the member to promote.

        Returns
        -------
        dict
            { "promoted": true }

        Raises
        ------
        APIError
            If the user is not in the group or the session lacks admin rights.
        """
        logger.info("[%s] Promoting %s to admin in group %s", session_id, phone, group_jid)
        return await self._request(
            "PATCH",
            f"/sessions/{session_id}/groups/{group_jid}/members/{phone}/promote",
        )

    async def remove_admin(
        self, session_id: str, group_jid: str, phone: str
    ) -> dict:
        """
        Demote a group admin back to regular member.

        Requires the session to be an admin (and not demoting the group owner).

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        phone : str
            Phone number in E.164 format of the admin to demote.

        Returns
        -------
        dict
            { "demoted": true }

        Raises
        ------
        APIError
            If the user is not an admin, is the group owner, or the session
            lacks admin rights.
        """
        logger.info("[%s] Demoting %s from admin in group %s", session_id, phone, group_jid)
        return await self._request(
            "PATCH",
            f"/sessions/{session_id}/groups/{group_jid}/members/{phone}/demote",
        )

    async def get_pending_requests(
        self, session_id: str, group_jid: str
    ) -> list:
        """
        Retrieve membership requests awaiting admin approval.

        Only relevant when the group has join-approval mode enabled
        (see update_group_settings with approval=True).

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.

        Returns
        -------
        list of dict
            [ { "phone": str, "requested_at": "<iso8601>" }, ... ]

        Raises
        ------
        APIError
            If the group is not in approval mode or session lacks admin rights.
        """
        logger.info("[%s] Fetching pending join requests for group %s", session_id, group_jid)
        result = await self._request(
            "GET",
            f"/sessions/{session_id}/groups/{group_jid}/pending-requests",
        )
        logger.info("[%s] %d pending request(s) found", session_id, len(result))
        return result

    async def approve_request(
        self, session_id: str, group_jid: str, phone: str
    ) -> dict:
        """
        Approve a pending join request for a group.

        Requires admin privileges and join-approval mode to be active.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        phone : str
            Phone number in E.164 format of the requester to approve.

        Returns
        -------
        dict
            { "approved": true }

        Raises
        ------
        APIError
            If no pending request exists for the phone number or the session
            lacks admin rights.
        """
        logger.info("[%s] Approving join request from %s for group %s", session_id, phone, group_jid)
        return await self._request(
            "POST",
            f"/sessions/{session_id}/groups/{group_jid}/pending-requests/{phone}/approve",
        )

    async def reject_request(
        self, session_id: str, group_jid: str, phone: str
    ) -> dict:
        """
        Reject a pending join request for a group.

        Requires admin privileges and join-approval mode to be active.

        Parameters
        ----------
        session_id : str
            Authenticated session to use.
        group_jid : str
            JID of the target group.
        phone : str
            Phone number in E.164 format of the requester to reject.

        Returns
        -------
        dict
            { "rejected": true }

        Raises
        ------
        APIError
            If no pending request exists for the phone number or the session
            lacks admin rights.
        """
        logger.info("[%s] Rejecting join request from %s for group %s", session_id, phone, group_jid)
        return await self._request(
            "POST",
            f"/sessions/{session_id}/groups/{group_jid}/pending-requests/{phone}/reject",
        )


# ---------------------------------------------------------------------------
# Quick-start demo (run only when executed directly)
# ---------------------------------------------------------------------------

async def _demo() -> None:
    """Minimal smoke-test against a locally running Baileys bridge."""
    async with WhatsAppClient(base_url="http://localhost:3000", rate_limit_delay=1.0) as client:
        session = "demo-session-001"

        # Step 1: request QR code
        try:
            qr_data = await client.connect_via_qr(session)
            print("QR code (base64 preview):", qr_data.get("qr_code", "")[:40], "…")
        except WhatsAppClientError as exc:
            print(f"Could not fetch QR (is the bridge running?): {exc}")
            return

        # Step 2: wait for the user to scan, then check status
        await asyncio.sleep(5)
        status = await client.get_connection_status(session)
        print("Connection status:", status)

        if status.get("connected"):
            groups = await client.get_all_groups(session)
            print(f"Belongs to {len(groups)} group(s).")

        await client.disconnect(session)


if __name__ == "__main__":
    asyncio.run(_demo())
