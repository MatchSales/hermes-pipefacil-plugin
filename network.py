"""Public-only media transport. Connect to validated addresses while retaining TLS SNI/Host."""

import ipaddress
import socket
import httpcore
import httpx

CORE_ERRORS = (httpcore.TimeoutException, httpcore.NetworkError, httpcore.ProtocolError, httpcore.ProxyError)


class PublicBackend(httpcore.SyncBackend):
    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(
                host, port, type=socket.SOCK_STREAM)))
        except OSError:
            raise httpcore.ConnectError("media_dns_failed") from None
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise httpcore.ConnectError("media_address_is_not_public")
        # A numeric IP has no second DNS lookup. TLS is subsequently established by
        # httpcore using the original URL hostname, not this pinned address.
        return super().connect_tcp(addresses[0], port, timeout, local_address, socket_options)


class Stream(httpx.SyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        try:
            yield from self.stream
        except CORE_ERRORS:
            raise httpx.TransportError("media_transport_failed") from None

    def close(self):
        self.stream.close()


class PublicTransport(httpx.BaseTransport):
    def __init__(self):
        self.pool = httpcore.ConnectionPool(network_backend=PublicBackend(), max_connections=1,
                                            max_keepalive_connections=0, retries=0)

    def handle_request(self, request):
        core = httpcore.Request(method=request.method, url=httpcore.URL(
            scheme=request.url.raw_scheme, host=request.url.raw_host,
            port=request.url.port, target=request.url.raw_path), headers=request.headers.raw,
            content=request.stream, extensions=request.extensions)
        try:
            response = self.pool.handle_request(core)
        except CORE_ERRORS:
            raise httpx.TransportError("media_transport_failed") from None
        return httpx.Response(response.status, headers=response.headers, stream=Stream(response.stream),
                              extensions=response.extensions)

    def close(self):
        self.pool.close()
