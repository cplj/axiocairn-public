"""HTTP policy for explicitly local development services; no DNS or redirects."""
import urllib.request
from urllib.parse import urlsplit


def validate_url(url):
    parts = urlsplit(url)
    # The sole private-address exception is the literal local dev-service host.
    # No hostname resolution means aliases and DNS rebinding cannot widen it.
    if (parts.scheme != 'http' or parts.hostname != '127.0.0.1'
            or not parts.port or parts.username or parts.password or parts.fragment
            or parts.netloc != f'127.0.0.1:{parts.port}'):
        raise ValueError('HTTP target must be http://127.0.0.1:PORT; other hosts are forbidden')
    return parts


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


class LocalOpener:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def open(self, request, timeout=5):
        validate_url(request if isinstance(request, str) else request.full_url)
        return self.opener.open(request, timeout=timeout)
