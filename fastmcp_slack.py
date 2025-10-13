from __future__ import annotations

from typing import Optional

import json
import os
import uuid
import hashlib
import hmac
import time
from mcp.server.fastmcp import FastMCP
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import psycopg2
from psycopg2.extras import RealDictCursor


mcp = FastMCP("slack")


DATABASE_URL = os.getenv("DATABASE_URL", "")


def _db_connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def _db_init() -> None:
    # Initialize tables if they don't exist
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    bot_token TEXT NOT NULL,
                    created_at BIGINT NOT NULL
                );
                """
            )
            conn.commit()


def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if not salt:
        salt = uuid.uuid4().hex
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return salt, dk.hex()


def _verify_password(password: str, salt: str, password_hash_hex: str) -> bool:
    _, computed_hex = _hash_password(password, salt)
    return hmac.compare_digest(computed_hex, password_hash_hex)


 


def _client(token: str) -> WebClient:
    # Allow referencing secrets via environment variables: env:VAR_NAME
    if token.startswith("env:"):
        env_name = token[4:]
        resolved = os.getenv(env_name)
        if not resolved:
            raise ValueError(f"env_var_missing:{env_name}")
        token = resolved

    if not token.startswith("xoxb-"):
        raise ValueError("Expected a Slack Bot token starting with xoxb-")
    return WebClient(token=token)


def _resolve_token_by_credentials(username: str, password: str) -> str:
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT salt, password_hash, bot_token FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
            if not row:
                raise ValueError("invalid_credentials")
            if not _verify_password(password, row["salt"], row["password_hash"]):
                raise ValueError("invalid_credentials")
            return row["bot_token"]


# Sessions removed; authenticate with username/password per call


@mcp.tool()
def sign_up(username: str, password: str, bot_token: str) -> str:
    """Register a new user with username, password, and Slack bot token."""
    _ = _client(bot_token)  # validate token format
    _db_init()
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM users WHERE username=%s", (username,))
            if cur.fetchone():
                return json.dumps({"ok": False, "error": "username_taken"}, ensure_ascii=False)
            salt, pwd_hash = _hash_password(password)
            cur.execute(
                "INSERT INTO users (username, salt, password_hash, bot_token, created_at) VALUES (%s, %s, %s, %s, %s)",
                (username, salt, pwd_hash, bot_token, int(time.time())),
            )
            conn.commit()
    try:
        print(f"sign_up: inserted user '{username}' into DB {DATABASE_URL}")
    except Exception:
        pass
    return json.dumps({"ok": True}, ensure_ascii=False)


@mcp.tool()
def login(username: str, password: str) -> str:
    """Verify credentials for an existing user."""
    _db_init()
    try:
        _ = _resolve_token_by_credentials(username, password)
        return json.dumps({"ok": True}, ensure_ascii=False)
    except Exception:
        return json.dumps({"ok": False, "error": "invalid_credentials"}, ensure_ascii=False)


@mcp.tool()
def get_user(username: str) -> str:
    """Debug: Return whether a user exists and when created (no secrets)."""
    _db_init()
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username, created_at FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
            if not row:
                return json.dumps({"exists": False})
            return json.dumps({"exists": True, "username": row["username"], "created_at": row["created_at"]})


# create_session removed (sessions not used)


# destroy_session removed (sessions not used)


@mcp.tool()
def list_dms(username: str, password: str, limit: int = 20) -> str:
    """List latest Slack IM channels (DMs) for the authenticated user."""
    token = _resolve_token_by_credentials(username, password)
    client = _client(token)
    try:
        resp = client.conversations_list(types="im", limit=limit)
        return json.dumps(resp.get("channels", []), ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


@mcp.tool()
def list_recent_messages(channel: str, username: str, password: str, limit: int = 20) -> str:
    """List recent messages in an IM channel for the authenticated user."""
    token = _resolve_token_by_credentials(username, password)
    client = _client(token)
    try:
        resp = client.conversations_history(channel=channel, limit=limit)
        return json.dumps(resp.get("messages", []), ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


@mcp.tool()
def send_reply(channel: str, text: str, username: str, password: str, thread_ts: Optional[str] = None) -> str:
    """Send a message to a channel (IM) or thread for the authenticated user."""
    token = _resolve_token_by_credentials(username, password)
    client = _client(token)
    try:
        resp = client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        return json.dumps({"ok": resp.get("ok", False), "channel": resp.get("channel"), "ts": resp.get("ts")}, ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


@mcp.tool()
def auto_reply_latest(username: str, password: str, text: Optional[str] = None) -> str:
    """Auto-reply to the most recent DM using provided text (or a default)."""
    if not text:
        text = "Thanks! I'll get back to you soon."
    token = _resolve_token_by_credentials(username, password)
    client = _client(token)
    try:
        ims = client.conversations_list(types="im", limit=1).get("channels", [])
        if not ims:
            return "error: no_im_channels"
        ch = ims[0]["id"]
        resp = client.chat_postMessage(channel=ch, text=text)
        return json.dumps({"channel": ch, "ts": resp.get("ts")}, ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


# if __name__ == "__main__":
#     host = os.getenv("FASTMCP_HOST", "0.0.0.0")
#     port = int(os.getenv("FASTMCP_PORT") or os.getenv("PORT") or 8001)

#     if os.getenv("STANDALONE_HTTP") == "1":
#         app = FastAPI()

#         @app.get("/tools/create_session")
#         def http_create_session(bot_token: str):
#             try:
#                 return json.loads(create_session(bot_token))
#             except Exception as e:
#                 raise HTTPException(400, str(e))

#         @app.get("/tools/destroy_session")
#         def http_destroy_session(session_id: str):
#             try:
#                 return json.loads(destroy_session(session_id))
#             except Exception as e:
#                 raise HTTPException(400, str(e))

#         @app.get("/tools/list_dms")
#         def http_list_dms(session_id: str | None = None, bot_token: str | None = None, limit: int = 20):
#             try:
#                 return json.loads(list_dms(bot_token=bot_token, session_id=session_id, limit=limit))
#             except Exception as e:
#                 raise HTTPException(400, str(e))

#         @app.get("/tools/list_recent_messages")
#         def http_list_recent_messages(channel: str, session_id: str | None = None, bot_token: str | None = None, limit: int = 20):
#             try:
#                 return json.loads(list_recent_messages(channel=channel, bot_token=bot_token, session_id=session_id, limit=limit))
#             except Exception as e:
#                 raise HTTPException(400, str(e))

#         @app.get("/tools/send_reply")
#         def http_send_reply(channel: str, text: str, thread_ts: str | None = None, session_id: str | None = None, bot_token: str | None = None):
#             try:
#                 return json.loads(send_reply(channel=channel, text=text, thread_ts=thread_ts, bot_token=bot_token, session_id=session_id))
#             except Exception as e:
#                 raise HTTPException(400, str(e))

#         @app.get("/tools/auto_reply_latest")
#         def http_auto_reply_latest(text: str | None = None, session_id: str | None = None, bot_token: str | None = None):
#             try:
#                 return json.loads(auto_reply_latest(text=text, bot_token=bot_token, session_id=session_id))
#             except Exception as e:
#                 raise HTTPException(400, str(e))

#         uvicorn.run(app, host=host, port=port)
#     else:
#         # Expose via streamable HTTP so Dify Cloud can call it as HTTP tools
#         mcp.settings.host = host
#         mcp.settings.port = port
#         mcp.run(transport="streamable-http")


if __name__ == "__main__":
    import os

    host = os.getenv("FASTMCP_HOST", "0.0.0.0")
    # On Render and Heroku-like platforms, PORT is provided by the platform.
    port = int(os.getenv("FASTMCP_PORT") or os.getenv("PORT") or 8010)

    # Ensure database schema exists on startup (creates users table if missing)
    try:
        _db_init()
    except Exception as e:
        print(f"Warning: DB init failed: {e}")

    mcp.settings.host = host
    mcp.settings.port = port
    print(f"Slack MCP server running at http://{host}:{port}")
    mcp.run(transport="streamable-http")
