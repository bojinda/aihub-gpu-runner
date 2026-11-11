"""Fixed-upstream TCP relay with durable admission, including manual ComfyUI.

Private backends MUST be unreachable outside the dedicated gateway/observer
UIDs. Existing connections remain counted; maintenance refuses to interrupt
them. A crashed relay leaves its permits for separate verified reconciliation.
No runtime firewall edits, arbitrary upstream selection or fail-open fallback.
"""
import select
import socket
import socketserver
from .core import Conflict


def relay(client, upstream):
    # Full-duplex; half-close is propagated rather than forgetting a live request.
    readers = [client, upstream]
    peers = {client: upstream, upstream: client}
    while readers:
        ready, _, _ = select.select(readers, [], [], 1)
        for src in ready:
            data = src.recv(65536)
            if not data:
                readers.remove(src)
                peers[src].shutdown(socket.SHUT_WR)
            else:
                peers[src].sendall(data)


def server(listen, upstream, gate):
    if not isinstance(upstream, tuple) or len(upstream) != 2:
        raise ValueError('fixed_upstream_required')
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                with gate.connection():
                    with socket.create_connection(upstream, timeout=5) as backend:
                        backend.settimeout(None)
                        relay(self.request, backend)
            except Conflict:
                self.request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 20\r\n\r\nRecovery maintenance')
            except Exception:
                # Permit lifetime ends only after upstream socket closes.
                # Failure to persist its close remains fail-closed.
                pass
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    return Server(listen, Handler)
