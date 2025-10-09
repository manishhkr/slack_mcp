from __future__ import annotations

from typing import Optional

import json
import os
import uuid
from mcp.server.fastmcp import FastMCP
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


mcp = FastMCP("slack")


# In-memory session storage mapping session_id -> bot_token
SESSION_TOKENS: dict[str, str] = {}


 


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


def _resolve_session_token(session_id: Optional[str]) -> str:
    if not session_id:
        raise ValueError("missing_session_id: call create_session(bot_token) first")
    token = SESSION_TOKENS.get(session_id)
    if not token:
        raise ValueError("invalid_session_id: create a new session via create_session")
    return token


@mcp.tool()
def create_session(bot_token: str) -> str:
    """Create a session and store the provided bot token. Returns session_id."""
    _ = _client(bot_token)  # validate format and token usability lazily
    session_id = uuid.uuid4().hex
    SESSION_TOKENS[session_id] = bot_token
    return json.dumps({"session_id": session_id})


@mcp.tool()
def destroy_session(session_id: str) -> str:
    """Delete a previously created session."""
    if session_id in SESSION_TOKENS:
        del SESSION_TOKENS[session_id]
        return json.dumps({"ok": True})
    return json.dumps({"ok": False, "error": "invalid_session_id"})


@mcp.tool()
def list_dms(bot_token: Optional[str] = None, session_id: Optional[str] = None, limit: int = 20) -> str:
    """List latest Slack IM channels (DMs). Requires a valid session_id from create_session."""
    if bot_token and not session_id:
        return "error: session_required - call create_session(bot_token) and pass session_id"
    token = _resolve_session_token(session_id)
    client = _client(token)
    try:
        resp = client.conversations_list(types="im", limit=limit)
        return json.dumps(resp.get("channels", []), ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


@mcp.tool()
def list_recent_messages(channel: str, bot_token: Optional[str] = None, session_id: Optional[str] = None, limit: int = 20) -> str:
    """List recent messages in an IM channel. Requires session_id from create_session."""
    if bot_token and not session_id:
        return "error: session_required - call create_session(bot_token) and pass session_id"
    token = _resolve_session_token(session_id)
    client = _client(token)
    try:
        resp = client.conversations_history(channel=channel, limit=limit)
        return json.dumps(resp.get("messages", []), ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


@mcp.tool()
def send_reply(channel: str, text: str, thread_ts: Optional[str] = None, bot_token: Optional[str] = None, session_id: Optional[str] = None) -> str:
    """Send a message to a channel (IM) or thread. Requires session_id from create_session."""
    if bot_token and not session_id:
        return "error: session_required - call create_session(bot_token) and pass session_id"
    token = _resolve_session_token(session_id)
    client = _client(token)
    try:
        resp = client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        return json.dumps({"ok": resp.get("ok", False), "channel": resp.get("channel"), "ts": resp.get("ts")}, ensure_ascii=False)
    except SlackApiError as e:
        return f"error: {e.response['error']}"


@mcp.tool()
def auto_reply_latest(text: Optional[str] = None, bot_token: Optional[str] = None, session_id: Optional[str] = None) -> str:
    """Auto-reply to the most recent DM using provided text (or a default). Requires session_id."""
    if not text:
        text = "Thanks! I'll get back to you soon."
    if bot_token and not session_id:
        return "error: session_required - call create_session(bot_token) and pass session_id"
    token = _resolve_session_token(session_id)
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

    mcp.settings.host = host
    mcp.settings.port = port
    print(f"Slack MCP server running at http://{host}:{port}")
    mcp.run(transport="streamable-http")
