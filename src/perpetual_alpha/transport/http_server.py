"""Bounded internal HTTP adapter. Authentication is independent of lease possession."""
import hashlib
import hmac
import json
import re
import socket
import sqlite3
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from ..forecast_validation import ForecastUnavailable
from ..journal import canonical
from ..runtime.ingress import IngressRejected, Principal
from .fixture_gateway import query_fixture
from .messages import (
    ClaimRequest,
    CompleteRequest,
    DataQueryRequest,
    ErrorResponse,
    ForecastSubmission,
    HealthResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    SubmissionResponse,
    parse_message,
)


def load_credentials(path):
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries or len(entries) > 100:
        raise ValueError("Expected a bounded nonempty credential list")
    result, workers = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"token", "worker_id", "producer_id", "producer_version", "role"}:
            raise ValueError("Invalid credential fields")
        token = entry["token"]
        if not isinstance(token, str) or len(token) < 32 or len(token) > 512 or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError("Credential token must be 32..512 ASCII characters without whitespace")
        principal = Principal.from_dict({k: v for k, v in entry.items() if k != "token"})
        hashed = hashlib.sha256(token.encode()).digest()
        if hashed in [item[0] for item in result] or principal.worker_id in workers:
            raise ValueError("Duplicate token or worker")
        result.append((hashed, principal))
        workers.add(principal.worker_id)
    return result


class AgentHTTPServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, address, queue, credentials, settings, *, data_gateway=None):
        self.queue, self.credentials, self.settings = queue, credentials, settings
        self.data_gateway = data_gateway
        self.slots = threading.BoundedSemaphore(settings["max_http_workers"])
        self.rates, self.rate_lock = {}, threading.Lock()
        self.ready = True
        super().__init__(address, Handler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(self.settings["http_timeout_ms"] / 1000)
        return sock, address

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        deadline = threading.Timer(self.settings["http_timeout_ms"] / 1000, expire)
        deadline.daemon = True
        deadline.start()
        try:
            super().process_request_thread(request, address)
        finally:
            deadline.cancel()
            self.slots.release()

    def handle_error(self, request, address):
        # Never log headers, bodies, tokens, or traceback with client data.
        pass

    def authenticate(self, headers):
        values = headers.get_all("Authorization", [])
        if len(values) != 1 or not values[0].startswith("Bearer ") or len(values[0]) > 520:
            raise IngressRejected("UNAUTHENTICATED", "Bearer authentication required")
        hashed = hashlib.sha256(values[0][7:].encode()).digest()
        for expected, principal in self.credentials:
            if hmac.compare_digest(hashed, expected):
                now = time.monotonic()
                with self.rate_lock:
                    requests = self.rates.setdefault(principal.worker_id, deque())
                    while requests and requests[0] < now - 1:
                        requests.popleft()
                    if len(requests) >= 40:
                        raise IngressRejected("LIMIT_EXCEEDED", "Request rate exceeded")
                    requests.append(now)
                return principal
        raise IngressRejected("UNAUTHENTICATED", "Invalid credential")


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentTransport/0.1"
    sys_version = ""

    def log_message(self, *args):
        pass

    def reply(self, status, message=None):
        payload = b"" if message is None else canonical(message.to_dict()).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if payload:
            self.wfile.write(payload)

    def body(self, cls):
        sizes = self.headers.get_all("Content-Length", [])
        if (len(sizes) != 1 or not sizes[0].isdigit() or self.headers.get("Transfer-Encoding")
                or self.headers.get("Content-Type", "").lower() != "application/json"):
            raise ValueError("Exact JSON content type and one content length required")
        size = int(sizes[0])
        if not 0 < size <= self.server.settings["max_http_body"]:
            raise IngressRejected("LIMIT_EXCEEDED", "Body size exceeded")
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise ValueError("Incomplete body")
        # Bound nesting BEFORE recursive decoder; quoted braces have no structural meaning.
        depth, quoted, escaped = 0, False, False
        for byte in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (91, 123):
                depth += 1
                if depth > 32:
                    raise ValueError("JSON nesting limit exceeded")
            elif byte in (93, 125):
                depth -= 1
        return parse_message(cls, raw, max_bytes=size)

    def dispatch(self):
        if self.command == "GET" and self.path == "/healthz":
            return self.reply(200 if self.server.ready else 503,
                              HealthResponse("ready" if self.server.ready else "unavailable"))
        principal = self.server.authenticate(self.headers)
        queue, settings = self.server.queue, self.server.settings
        if self.command == "POST" and self.path == "/v1/jobs/claim":
            if not self.server.ready:
                raise IngressRejected("UNAVAILABLE", "Core is paused")
            result = queue.claim(principal, self.body(ClaimRequest), lease_ms=settings["lease_ms"])
            return self.reply(204 if result is None else 200, result)
        match = re.fullmatch(r"/v1/jobs/([^/?#]+)/([a-z/]+)", self.path)
        if not match:
            raise IngressRejected("NOT_FOUND", "Unknown route")
        job_id, action = unquote(match[1]), match[2]
        if self.command == "GET" and action == "feedback":
            return self.reply(200, queue.feedback(principal, job_id))
        classes = {"heartbeat": HeartbeatRequest, "data/query": DataQueryRequest,
                   "forecast": ForecastSubmission, "complete": CompleteRequest}
        if self.command != "POST" or action not in classes:
            raise IngressRejected("NOT_FOUND", "Unknown route")
        message = self.body(classes[action])
        if message.lease.job_id != job_id:
            raise ValueError("Route/body job mismatch")
        if action == "heartbeat":
            return self.reply(200, HeartbeatResponse(queue.heartbeat(principal, message.lease,
                                                                    lease_ms=settings["lease_ms"])))
        if action == "data/query":
            if self.server.data_gateway is not None and message.data_request.source == "hyperliquid-testnet/par":
                return self.reply(200, self.server.data_gateway.query(principal, message))
            return self.reply(200, query_fixture(queue, principal, message))
        if action == "complete":
            return self.reply(200, queue.complete(principal, message))
        receipt, replay = queue.submit(principal, message, replay_status=True)
        return self.reply(200 if replay else 202, SubmissionResponse(receipt))

    def do_GET(self):
        try:
            self.dispatch()
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception as error:
            code = getattr(error, "code", "INVALID_REQUEST" if isinstance(error, ValueError) else "UNAVAILABLE")
            if isinstance(error, ForecastUnavailable):
                code = "INVALID_FORECAST"
            if isinstance(error, sqlite3.Error):
                code = "UNAVAILABLE"
            status = {"UNAUTHENTICATED": 401, "FORBIDDEN": 403, "NOT_FOUND": 404,
                      "LEASE_LOST": 409, "ID_CONFLICT": 409, "STALE_RESULT": 409,
                      "EXPIRED": 410, "LIMIT_EXCEEDED": 429, "UNAVAILABLE": 503}.get(code, 400)
            try:
                self.reply(status, ErrorResponse("http", code, "Request rejected: " + code,
                                                code in {"UNAVAILABLE", "LIMIT_EXCEEDED"}))
            except (OSError, ValueError):
                self.close_connection = True

    do_POST = do_GET
