from __future__ import annotations
from typing import Optional
import os
import json
import uuid
import hashlib
import hmac
import time
from mcp.server.fastmcp import FastMCP
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import psycopg2
from psycopg2.extras import RealDictCursor

# -----------------------------
# MCP Server Setup
# -----------------------------
mcp = FastMCP("slack")

# -----------------------------
# Database Configuration (from environment variables)
# -----------------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "slack")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "Manish@123")  # local default for testing

# -----------------------------
# Database Utilities
# -----------------------------
def _db_connect():
    """Return a new database connection with a RealDictCursor."""
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            cursor_factory=RealDictCursor
        )
    except Exception as e:
        raise RuntimeError(f"Database connection failed: {e}")


def _db_init() -> None:
    """Create the users table if it does not exist."""
    try:
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
        print("Database initialized successfully.")
    except Exception as e:
        print(f"Warning: DB initialization failed: {e}")

# -----------------------------
# Password Utilities
# -----------------------------
def _hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if not salt:
        salt = uuid.uuid4().hex
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000
    )
    return salt, dk.hex()


def _verify_password(password: str, salt: str, password_hash_hex: str) -> bool:
    _, computed_hex = _hash_password(password, salt)
    return hmac.compare_digest(computed_hex, password_hash_hex)

# -----------------------------
# Slack Utilities
# -----------------------------
def _client(token: str) -> WebClient:
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
            cur.execute(
                "SELECT salt, password_hash, bot_token FROM users WHERE username=%s",
                (username,)
            )
            row = cur.fetchone()
            if not row or not _verify_password(password, row["salt"], row["password_hash"]):
                raise ValueError("invalid_credentials")
            return row["bot_token"]

# -----------------------------
# MCP Tools
# -----------------------------
@mcp.tool()
def sign_up(username: str, password: str, bot_token: str) -> str:
    _ = _client(bot_token)
    _db_init()
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username FROM users WHERE username=%s", (username,))
            if cur.fetchone():
                return json.dumps({"ok": False, "error": "username_taken"}, ensure_ascii=False)
            salt, pwd_hash = _hash_password(password)
            cur.execute(
                "INSERT INTO users (username, salt, password_hash, bot_token, created_at) VALUES (%s,%s,%s,%s,%s)",
                (username, salt, pwd_hash, bot_token, int(time.time()))
            )
            conn.commit()
    print(f"User '{username}' inserted into DB successfully.")
    return json.dumps({"ok": True}, ensure_ascii=False)


@mcp.tool()
def login(username: str, password: str) -> str:
    _db_init()
    try:
        _resolve_token_by_credentials(username, password)
        return json.dumps({"ok": True}, ensure_ascii=False)
    except ValueError:
        return json.dumps({"ok": False, "error": "invalid_credentials"}, ensure_ascii=False)


@mcp.tool()
def get_user(username: str) -> str:
    _db_init()
    with _db_connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT username, created_at FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
            if not row:
                return json.dumps({"exists": False})
            return json.dumps({"exists": True, "username": row["username"], "created_at": row["created_at"]})


@mcp.tool()
def list_dms(username: str, password: str, limit: int = 20) -> str:
    try:
        token = _resolve_token_by_credentials(username, password)
        client = _client(token)
        resp = client.conversations_list(types="im", limit=limit)
        return json.dumps(resp.get("channels", []), ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


@mcp.tool()
def list_recent_messages(channel: str, username: str, password: str, limit: int = 20) -> str:
    try:
        token = _resolve_token_by_credentials(username, password)
        client = _client(token)
        resp = client.conversations_history(channel=channel, limit=limit)
        return json.dumps(resp.get("messages", []), ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


@mcp.tool()
def send_reply(channel: str, text: str, username: str, password: str, thread_ts: Optional[str] = None) -> str:
    try:
        token = _resolve_token_by_credentials(username, password)
        client = _client(token)
        resp = client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        return json.dumps({"ok": resp.get("ok", False), "channel": resp.get("channel"), "ts": resp.get("ts")}, ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


@mcp.tool()
def auto_reply_latest(username: str, password: str, text: Optional[str] = None) -> str:
    if not text:
        text = "Thanks! I'll get back to you soon."
    try:
        token = _resolve_token_by_credentials(username, password)
        client = _client(token)
        ims = client.conversations_list(types="im", limit=1).get("channels", [])
        if not ims:
            return "error: no_im_channels"
        ch = ims[0]["id"]
        resp = client.chat_postMessage(channel=ch, text=text)
        return json.dumps({"channel": ch, "ts": resp.get("ts")}, ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"

# -----------------------------
# MCP Server Startup
# -----------------------------
if __name__ == "__main__":
    host = os.getenv("FASTMCP_HOST", "0.0.0.0")
    port = int(os.getenv("FASTMCP_PORT") or os.getenv("PORT") or 8010)

    # Initialize DB at startup
    _db_init()

    mcp.settings.host = host
    mcp.settings.port = port
    print(f"Slack MCP server running at http://{host}:{port}")
    mcp.run(transport="streamable-http")
