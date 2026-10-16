"""Finite timeouts and strict messages; never forward bearer credentials across redirects."""
import urllib.error
import urllib.parse
import urllib.request

from ..journal import canonical
from .messages import ErrorResponse, parse_message


class RemoteError(Exception):
    def __init__(self, response):
        self.code, self.retryable = response.code, response.retryable
        super().__init__(response.code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HTTPClient:
    def __init__(self, base_url, token, *, timeout=5, max_bytes=524288):
        url = urllib.parse.urlsplit(base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {"", "/"} or not 0 < timeout <= 60):
            raise ValueError("Expected an HTTP(S) origin and finite timeout")
        if not isinstance(token, str) or not token.isascii() or any(c.isspace() for c in token):
            raise ValueError("Invalid token")
        self.base_url, self.token, self.timeout, self.max_bytes = base_url.rstrip("/"), token, timeout, max_bytes
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, path, cls, message=None):
        if not path.startswith("/v1/jobs/") or "?" in path or "#" in path:
            raise ValueError("Unsupported client route")
        raw = None if message is None else canonical(message.to_dict()).encode()
        request = urllib.request.Request(self.base_url + path, data=raw,
                                         headers={"Authorization": "Bearer " + self.token,
                                                  "Content-Type": "application/json"})
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            with error:
                body = error.read(self.max_bytes + 1)
            if error.code in {301, 302, 303, 307, 308}:
                raise ValueError("Redirect refused") from None
            raise RemoteError(parse_message(ErrorResponse, body, max_bytes=self.max_bytes)) from None
        with response:
            if response.status == 204:
                return None
            body = response.read(self.max_bytes + 1)
        return parse_message(cls, body, max_bytes=self.max_bytes)
