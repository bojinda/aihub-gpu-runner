"""Synthetic local HTTP regression: socket timeout != total request deadline.

Run from the runner checkout with PYTHONPATH pointing at the reviewed runner.
No production services, inference, Docker, or external network are involved.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest

from aihub_gpu_runner.backends import HTTPTransport, Uncertain


class SlowTrickleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/queue":
            self.send_error(404)
            return
        payload = b'{"queue_running":[],"queue_pending":[]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        for byte in payload:
            try:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            time.sleep(.06)  # Faster than the socket timeout; longer in total.

    def log_message(self, *args):
        pass


class WallClockHTTPDeadlineRegression(unittest.TestCase):
    def test_queue_http_has_overall_wall_clock_limit(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowTrickleHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            transport = HTTPTransport(f"http://127.0.0.1:{server.server_port}")
            started = time.monotonic()
            try:
                transport.json("GET", "/queue", timeout=.3)
            except Uncertain:
                pass  # Expected when the full response cannot finish in time.
            duration = time.monotonic() - started
            self.assertLess(
                duration, .95,
                f"Request exceeded its 0.3s deadline; observed {duration:.2f}s "
                "because socket timeout was reset by trickled response bytes"
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
