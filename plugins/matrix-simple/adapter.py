"""Simple Matrix platform adapter for Hermes.

Text-only, no E2EE. Supports @mentions and reactions (read + send).
"""

import asyncio
import logging
import os
import re
from typing import Any, Dict, Optional

try:
    from mautrix.client import Client as MautrixClient
    from mautrix.api import HTTPAPI
    MAUTRIX_OK = True
except ImportError:
    MAUTRIX_OK = False
    MautrixClient = None

from gateway.config import Platform, PlatformConfig
def _markdown_to_html(text):
    """Convert basic markdown to Matrix HTML."""
    import re
    t = text
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    t = t.replace("\n", "<br/>")
    return t
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

LONG_POLL_TIMEOUT = 30


def check_requirements() -> bool:
    if not MAUTRIX_OK:
        return False
    hs = os.getenv("MATRIX_HOMESERVER", "").strip()
    user = os.getenv("MATRIX_USERNAME", "").strip()
    pw = os.getenv("MATRIX_PASSWORD", "").strip()
    return bool(hs and user and pw)


def validate_config(config) -> bool:
    return check_requirements()


def is_connected(config) -> bool:
    return check_requirements()


class MatrixSimpleAdapter(BasePlatformAdapter):
    def __init__(self, config: PlatformConfig):
        platform = Platform("matrix-simple")
        super().__init__(config=config, platform=platform)

        self._homeserver = os.getenv("MATRIX_HOMESERVER", "").strip()
        self._username = os.getenv("MATRIX_USERNAME", "").strip()
        self._password = os.getenv("MATRIX_PASSWORD", "").strip()
        self._home_room = os.getenv("MATRIX_HOME_ROOM", "").strip()
        self._require_mention = os.getenv("MATRIX_REQUIRE_MENTION", "false").strip().lower() in ("true", "1", "yes")

        self._client: Optional[Any] = None
        self._token: Optional[str] = None
        self._sync_task: Optional[asyncio.Task] = None
        self._closing = False
        self._seen_events: set = set()
        self._last_event_ts: float = 0.0

        self._mention_pattern = re.compile(
            r"@?" + re.escape(self._username) + r"(?::[a-zA-Z0-9.-]+)?\b",
            re.IGNORECASE,
        )
        logger.info(
            "Matrix: require_mention=%s, username=%s",
            self._require_mention, self._username,
        )

    def _is_mentioned(self, body: str) -> bool:
        return bool(self._mention_pattern.search(body))

    async def connect(self) -> bool:
        try:
            import urllib.request, json, uuid

            login_url = f"{self._homeserver}/_matrix/client/v3/login"
            login_data = json.dumps({
                "type": "m.login.password",
                "identifier": {"type": "m.id.user", "user": self._username},
                "password": self._password,
                "initial_device_display_name": "Hermes Bridge",
            }).encode()
            req = urllib.request.Request(login_url, data=login_data,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = json.loads(r.read().decode())

            self._token = resp["access_token"]
            user_id = resp.get("user_id", f"@{self._username}:unknown")
            logger.info("Matrix: logged in as %s", user_id)

            api = HTTPAPI(base_url=self._homeserver, token=self._token)
            self._client = MautrixClient(api=api)

            if self._home_room:
                join_url = f"{self._homeserver}/_matrix/client/v3/rooms/{self._home_room}/join"
                join_req = urllib.request.Request(join_url, data=b"{}",
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self._token}"},
                    method="POST")
                try:
                    with urllib.request.urlopen(join_req, timeout=10):
                        logger.info("Matrix: joined room %s", self._home_room)
                except Exception as e:
                    logger.warning("Matrix: room join skipped: %s", e)

            self._closing = False
            self._sync_task = asyncio.create_task(self._sync_loop())
            self._mark_connected()
            logger.info("Matrix: connected, sync started")
            return True

        except Exception as e:
            logger.error("Matrix: connect failed: %s", e)
            return False

    async def _sync_loop(self):
        import urllib.request, json, time as _time

        next_batch = None
        while not self._closing:
            try:
                params = f"?timeout={LONG_POLL_TIMEOUT * 1000}"
                if next_batch:
                    params += f"&since={next_batch}"

                sync_url = f"{self._homeserver}/_matrix/client/v3/sync{params}"
                sync_req = urllib.request.Request(sync_url,
                    headers={"Authorization": f"Bearer {self._token}"})

                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(None, self._do_sync, sync_req)
                if data is None:
                    await asyncio.sleep(5)
                    continue

                next_batch = data.get("next_batch", next_batch)

                rooms = data.get("rooms", {}).get("join", {})
                for rid, rdata in rooms.items():
                    timeline = rdata.get("timeline", {}).get("events", [])
                    for ev in timeline:
                        eid = ev.get("event_id", "")
                        if eid in self._seen_events:
                            continue
                        self._seen_events.add(eid)
                        if len(self._seen_events) > 5000:
                            self._seen_events = set(list(self._seen_events)[-2000:])

                        etype = ev.get("type", "")

                        # --- Reactions ---
                        if etype == "m.reaction":
                            await self._handle_reaction(ev, rid)
                            continue

                        if etype != "m.room.message":
                            continue

                        sender = ev.get("sender", "")
                        sender_name = sender.split(":")[0].lstrip("@")
                        if sender_name in ('conduit', self._username):
                            continue
                        body = ev.get("content", {}).get("body", "").strip()
                        if not body:
                            continue

                        age = ev.get("unsigned", {}).get("age", 0)
                        ts = _time.time() - age / 1000.0
                        if ts <= self._last_event_ts:
                            continue
                        self._last_event_ts = ts

                        mentioned = self._is_mentioned(body)

                        if self._require_mention and not mentioned:
                            logger.debug(
                                "Matrix: skipping unmentioned from %s: %s",
                                sender_name, body[:80],
                            )
                            continue

                        tag = "DM" if mentioned else "ambient"
                        logger.info(
                            "Matrix [%s] %s: %s", tag, sender_name, body[:100],
                        )

                        from gateway.session import SessionSource
                        source = SessionSource(
                            platform=self.platform,
                            chat_id=rid,
                            user_id=sender,
                            user_name=sender_name,
                        )
                        event = MessageEvent(
                            text=body,
                            message_type=MessageType.TEXT,
                            source=source,
                            message_id=eid,
                            raw_message=ev,
                        )
                        await self.handle_message(event)

            except Exception as e:
                logger.error("Matrix sync error: %s", e)
                await asyncio.sleep(5)

    async def _handle_reaction(self, ev: dict, room_id: str):
        """Process an incoming reaction event."""
        relates_to = ev.get("content", {}).get("m.relates_to", {})
        if relates_to.get("rel_type") != "m.annotation":
            return
        reacted_event_id = relates_to.get("event_id", "")
        emoji = relates_to.get("key", "")
        sender = ev.get("sender", "")
        sender_name = sender.split(":")[0].lstrip("@")

        if sender_name in ('conduit', self._username):
            return

        logger.info(
            "Matrix [reaction] %s reacted %s to %s",
            sender_name, emoji, reacted_event_id[:20],
        )

        # Dispatch as a reaction event so the gateway can act on it
        from gateway.session import SessionSource
        source = SessionSource(
            platform=self.platform,
            chat_id=room_id,
            user_id=sender,
            user_name=sender_name,
        )
        event = MessageEvent(
            text=f"/reaction {emoji} -> {reacted_event_id}",
            message_type=MessageType.TEXT,
            source=source,
            message_id=ev.get("event_id", ""),
            raw_message={
                **ev,
                "_reaction_emoji": emoji,
                "_reaction_target": reacted_event_id,
                "_reaction_sender": sender_name,
            },
        )
        await self.handle_message(event)

    def _do_sync(self, req):
        import urllib.request, json
        try:
            with urllib.request.urlopen(req, timeout=LONG_POLL_TIMEOUT + 15) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    async def disconnect(self) -> None:
        self._closing = True
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
        self._token = None
        logger.info("Matrix: disconnected")

    async def send(
        self, chat_id: str, content: str, reply_to=None, metadata=None,
    ) -> SendResult:
        if not self._token:
            return SendResult(success=False, error="Not connected")

        try:
            import urllib.request, json, uuid

            # Check if this is a reaction request
            if metadata and metadata.get("reaction"):
                emoji = str(metadata["reaction"])
                target = str(metadata.get("reaction_target", ""))
                return await self._send_reaction(chat_id, target, emoji)

            txn = str(uuid.uuid4())
            send_url = (
                f"{self._homeserver}/_matrix/client/v3/rooms/{chat_id}"
                f"/send/m.room.message/{txn}"
            )
            plain = content
            html = _markdown_to_html(content)
            payload = json.dumps({
                "msgtype": "m.text",
                "body": plain,
                "format": "org.matrix.custom.html",
                "formatted_body": html,
            }).encode()
            send_req = urllib.request.Request(send_url, data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._token}",
                },
                method="PUT")
            with urllib.request.urlopen(send_req, timeout=15) as r:
                resp = json.loads(r.read().decode())
            return SendResult(
                success=True,
                message_id=resp.get("event_id", ""),
            )
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def _send_reaction(
        self, room_id: str, target_event_id: str, emoji: str,
    ) -> SendResult:
        """Send a reaction (emoji) to a specific event."""
        try:
            import urllib.request, json, uuid
            txn = str(uuid.uuid4())
            react_url = (
                f"{self._homeserver}/_matrix/client/v3/rooms/{room_id}"
                f"/send/m.reaction/{txn}"
            )
            payload = json.dumps({
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": target_event_id,
                    "key": emoji,
                },
            }).encode()
            react_req = urllib.request.Request(react_url, data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._token}",
                },
                method="PUT")
            with urllib.request.urlopen(react_req, timeout=15) as r:
                resp = json.loads(r.read().decode())
            logger.info(
                "Matrix: reacted %s to %s", emoji, target_event_id[:20],
            )
            return SendResult(
                success=True,
                message_id=resp.get("event_id", ""),
            )
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "group"}


def register(ctx) -> None:
    ctx.register_platform(
        name="matrix-simple",
        label="Matrix (simple)",
        adapter_factory=lambda cfg: MatrixSimpleAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["MATRIX_HOMESERVER", "MATRIX_USERNAME", "MATRIX_PASSWORD"],
        install_hint="pip install mautrix",
        cron_deliver_env_var="MATRIX_HOME_ROOM",
        allowed_users_env="MATRIX_ALLOWED_USERS",
        max_message_length=4000,
        emoji="🟦",
    )
