"""Sign in without copying anything: a one-shot local listener that receives the browser's return from ArcGIS.

The standard way for a command-line tool to sign a user in (RFC 8252, with PKCE as in RFC 7636): the tool opens the ArcGIS sign-in
page in the browser, the user clicks Allow, and ArcGIS sends the browser back to ``http://localhost:<port>/storymap-gen-callback``
with a short-lived one-time *code*. This module listens for exactly that one request and hands the code back; the tool then
exchanges it (together with a secret it made up for this login, the PKCE verifier) for the access token. Nothing secret is shown
in the browser's address bar, typed, or pasted, and an intercepted code is useless without the verifier.

The listener is deliberately narrow: it binds to 127.0.0.1 only, answers only the callback path, only when the Host header is the
local one (a web page cannot trick the browser into talking to it under another name), only accepts the ``state`` value the tool
generated (so a request from anywhere else cannot complete or jam the login), never echoes anything it receives, writes no log, and
stops after one good callback or after a timeout.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

CALLBACK_PATH = "/storymap-gen-callback"
DEFAULT_PORT = 8765
TIMEOUT_SECONDS = 180

_PAGE = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>storymap-gen</title>"
         "<style>body{font:16px system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem;color:#222}</style></head>"
         "<body><h1>{title}</h1><p>{body}</p></body></html>")


class LoginError(RuntimeError):
    """The sign-in did not complete (refused, timed out, or the port was not free)."""


def pkce_pair() -> tuple[str, str]:
    """A fresh (verifier, challenge): the verifier stays on this machine, the challenge (its SHA-256) goes to ArcGIS."""
    verifier = secrets.token_urlsafe(64)[:96]
    return verifier, challenge_for(verifier)


def challenge_for(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def new_state() -> str:
    return secrets.token_urlsafe(32)


def redirect_uri(port: int = DEFAULT_PORT) -> str:
    return f"http://localhost:{port}{CALLBACK_PATH}"


def _page(title: str, body: str) -> bytes:
    return _PAGE.replace("{title}", title).replace("{body}", body).encode("utf-8")


def wait_for_code(state: str, port: int = DEFAULT_PORT, timeout: float = TIMEOUT_SECONDS,
                  on_ready: Callable[[], None] | None = None) -> str:
    """Listen on 127.0.0.1:<port> for the one callback that carries ``state`` and return its authorization code.

    ``on_ready`` runs once the listener is bound (open the browser there, so the callback cannot arrive before anyone listens).
    Raises ``LoginError`` if the user refused, if nothing valid arrived in ``timeout`` seconds, or if the port is not free.
    """
    result: dict[str, str] = {}
    hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "storymap-gen"
        sys_version = ""

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature of the base class
            return                                               # no log: the request line contains the one-time code

        def _send(self, status: int, title: str, body: str) -> None:
            data = _page(title, body)
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - name fixed by the base class
            if (self.headers.get("Host") or "").lower() not in hosts:
                return self._send(400, "Not allowed", "This address is only for the sign-in on this computer.")
            parts = urlsplit(self.path)
            if parts.path != CALLBACK_PATH:
                return self._send(404, "Not found", "Nothing here.")
            query = parse_qs(parts.query)
            got = (query.get("state") or [""])[0]
            if not hmac.compare_digest(got.encode("utf-8"), state.encode("utf-8")):
                return self._send(400, "Not the sign-in you started", "This request does not belong to the sign-in in your terminal, so it was ignored.")
            if "error" in query:
                raw = query["error"][0] or "access_denied"
                result["error"] = raw if re.fullmatch(r"[a-z_]{1,40}", raw) else "unknown_error"      # a standard error code, never free text
                return self._send(200, "Sign-in cancelled", "ArcGIS did not grant access. You can close this tab and return to the terminal.")
            code = (query.get("code") or [""])[0]
            if not code or len(code) > 4096:
                return self._send(400, "Missing code", "ArcGIS did not send a sign-in code. Return to the terminal.")
            result["code"] = code
            return self._send(200, "Signed in", "You can close this tab and return to the terminal.")

        def _not_allowed(self) -> None:
            self._send(405, "Not allowed", "Only the browser's return from ArcGIS is accepted here.")

        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _not_allowed       # nothing but a GET can complete the sign-in

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        raise LoginError(f"port {port} on this computer is not available ({e.strerror or type(e).__name__}); close what is using it, "
                         "or register another redirect URL and pass --port") from None
    server.timeout = 0.5
    try:
        if on_ready:
            on_ready()
        deadline = time.monotonic() + timeout
        while not result and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "code" in result:
        return result["code"]
    if "error" in result:
        raise LoginError(f"ArcGIS did not grant access ({result['error']})")
    raise LoginError("timed out waiting for the browser; run the command again, or use --manual to paste the address yourself")
