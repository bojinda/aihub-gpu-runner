"""Real loopback reads: deadlines cover headers/body, not just socket silence."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from aihub_gpu_runner.backends import ComfyBackend, HTTPTransport, Target, Uncertain
from aihub_gpu_runner import http_deadline
from aihub_gpu_runner.backends import remaining
from aihub_gpu_runner.recovery import RecoveryObserver
from tests import test_wallpaper_lifecycle as lifecycle_fixtures


@contextmanager
def local_http(responses, context=None, *, concurrent=False):
    calls, stop = [], threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            route = urlsplit(self.path).path
            calls.append((self.command, route))
            spec = responses[route]
            body = spec.get('body', b'{}')
            if stop.wait(spec.get('before_headers', 0)):
                return
            headers = (f"HTTP/1.0 {spec.get('status', 200)} Test\r\n"
                       f"Content-Type: {spec.get('media', 'application/json')}\r\n").encode()
            if spec.get('chunked'):
                headers += b'Transfer-Encoding: chunked\r\n'
                body = b''.join(b'1\r\n' + bytes([b]) + b'\r\n' for b in body) + b'0\r\n\r\n'
            elif not spec.get('until_eof'):
                headers += f'Content-Length: {len(body)}\r\n'.encode()
            if spec.get('status') == 302:
                headers += b'Location: /redirected\r\n'
            headers += b'\r\n'
            try:
                for data, delay in ((headers, spec.get('header_byte_delay', 0)),
                                    (body, spec.get('body_byte_delay', 0))):
                    if delay:
                        for byte in data:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            if stop.wait(delay):
                                return
                    else:
                        self.wfile.write(data)
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = do_POST = respond

    server_type = ThreadingHTTPServer if concurrent else HTTPServer
    server = server_type(('127.0.0.1', 0), Handler)
    # Join concurrent handlers after stop wakes their synthetic response delays.
    server.daemon_threads = False
    if context is not None:
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        yield f"{'https' if context else 'http'}://127.0.0.1:{server.server_port}", calls
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive()


class HTTPReadDeadlineTests(unittest.TestCase):
    def assert_bounded_failure(self, spec, *, binary=False):
        with local_http({'/queue': spec}) as (url, calls):
            transport = HTTPTransport(url)
            threads = set(threading.enumerate())
            started = time.monotonic()
            with self.assertRaises(Uncertain):
                if binary:
                    transport.binary('/queue', {}, timeout=.2)
                else:
                    transport.json('GET', '/queue', timeout=.2)
            duration = time.monotonic() - started
            self.assertLess(duration, .65, duration)
            self.assertGreater(duration, .15, 'Must actually exercise the timeout')
            self.assertEqual(calls, [('GET', '/queue')], 'Never retry the HTTP request')
            self.assertEqual(set(threading.enumerate()), threads, 'No request helper threads')

    def test_delayed_response_headers_are_bounded(self):
        self.assert_bounded_failure({'before_headers': 2})

    def test_trickled_response_headers_are_bounded(self):
        self.assert_bounded_failure({'header_byte_delay': .025})

    def test_trickled_content_length_body_is_bounded(self):
        self.assert_bounded_failure({'body': b'{"queue_running":[],"queue_pending":[]}',
                                     'body_byte_delay': .025})

    def test_trickled_chunked_body_is_bounded(self):
        self.assert_bounded_failure({'body': b'{"queue_running":[],"queue_pending":[]}',
                                     'chunked': True, 'body_byte_delay': .015})

    def test_trickled_body_until_eof_is_bounded(self):
        self.assert_bounded_failure({'body': b'{"queue_running":[],"queue_pending":[]}',
                                     'until_eof': True, 'body_byte_delay': .025})

    def test_trickled_artifact_body_is_bounded(self):
        self.assert_bounded_failure({'body': b'\x89PNG\r\n\x1a\nsynthetic-image',
                                     'body_byte_delay': .025}, binary=True)

    def test_header_and_body_time_share_one_budget(self):
        self.assert_bounded_failure({'before_headers': .12, 'body_byte_delay': .025,
                                     'body': b'{"queue_running":[],"queue_pending":[]}'})

    def test_ordinary_prompt_history_and_artifact_complete(self):
        png = b'\x89PNG\r\n\x1a\nsynthetic-image'
        history = {'owned-prompt': {'status': {'completed': True, 'status_str': 'success'},
            'outputs': {'2': {'images': [{'filename': 'synthetic.png', 'type': 'output'}]}}}}
        responses = {'/prompt': {'body': b'{"prompt_id":"owned-prompt"}'},
                     '/history/owned-prompt': {'body': json.dumps(history).encode()},
                     '/view': {'body': png, 'media': 'image/png'}}
        with local_http(responses) as (url, calls):
            transport = HTTPTransport(url)
            target = Target('wallpaper', 'comfy', ('gpu0',), url, output_node='2')
            self.assertEqual(transport.json('POST', '/prompt', {})['prompt_id'], 'owned-prompt')
            execution = ComfyBackend(target, transport).reconcile('owned-prompt', time.monotonic() + 1)
            self.assertEqual(execution.artifact, png)
            self.assertIsNone(execution.error_code)
            self.assertEqual(calls, [('POST', '/prompt'), ('GET', '/history/owned-prompt'), ('GET', '/view')])

    def test_response_size_limit_survives_all_body_framings(self):
        for framing in ({}, {'chunked': True}, {'until_eof': True}):
            with self.subTest(framing=framing), local_http({'/queue': {'body': b'x' * 40, **framing}}) as (url, _):
                with self.assertRaises(Uncertain) as caught:
                    HTTPTransport(url, max_bytes=32).json('GET', '/queue')
                self.assertEqual(caught.exception.code, 'backend_response_too_large')

    def test_json_remains_strict_and_requires_an_object(self):
        for body in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]', b'bad json', b'\xff'):
            with self.subTest(body=body), local_http({'/queue': {'body': body}}) as (url, _):
                with self.assertRaises(Uncertain) as caught:
                    HTTPTransport(url).json('GET', '/queue')
                self.assertEqual(caught.exception.code, 'invalid_backend_json')

    def test_redirect_refused_without_following_or_retry(self):
        with local_http({'/queue': {'status': 302}}) as (url, calls):
            with self.assertRaises(Uncertain) as caught:
                HTTPTransport(url).json('GET', '/queue')
            self.assertEqual(caught.exception.code, 'backend_redirect_forbidden')
            self.assertEqual(calls, [('GET', '/queue')])

    def test_environment_proxies_are_ignored(self):
        with local_http({'/queue': {}}) as (url, calls), patch.dict('os.environ', {
            'http_proxy': 'http://127.0.0.1:1', 'https_proxy': 'http://127.0.0.1:1',
            'all_proxy': 'http://127.0.0.1:1', 'no_proxy': '',
            'HTTP_PROXY': 'http://127.0.0.1:1', 'HTTPS_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}):
            self.assertEqual(HTTPTransport(url).json('GET', '/queue'), {})
            self.assertEqual(calls, [('GET', '/queue')])

    def test_hostname_resolution_and_connection_share_the_deadline(self):
        with local_http({'/queue': {}}) as (url, calls):
            self.assertEqual(HTTPTransport(url.replace('127.0.0.1', 'localhost')).json('GET', '/queue'), {})
            self.assertEqual(calls, [('GET', '/queue')])

    def test_slow_hostname_resolver_is_killed_and_reaped_within_budget(self):
        run, popen, children = subprocess.run, subprocess.Popen, []

        def slow_resolver(args, **kwargs):
            return run([sys.executable, '-I', '-c', 'import time; time.sleep(10)'], **kwargs)

        def capture_child(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            return child

        with local_http({'/queue': {}}) as (url, calls), \
             patch.object(http_deadline.subprocess, 'run', side_effect=slow_resolver), \
             patch.object(http_deadline.subprocess, 'Popen', side_effect=capture_child):
            started = time.monotonic()
            with self.assertRaises(Uncertain):
                HTTPTransport(url.replace('127.0.0.1', 'localhost')).json('GET', '/queue', timeout=.2)
            self.assertLess(time.monotonic() - started, .65)
            self.assertEqual(calls, [])
            self.assertEqual(len(children), 1)
            self.assertIsNotNone(children[0].poll())

    def test_invalid_read_deadlines_never_issue_a_request(self):
        with local_http({'/queue': {}}) as (url, calls):
            for timeout in (None, 0, -1, float('inf'), float('nan'), True):
                with self.subTest(timeout=timeout), self.assertRaises(Uncertain) as caught:
                    HTTPTransport(url).json('GET', '/queue', timeout=timeout)
                self.assertEqual(caught.exception.code, 'backend_deadline')
            self.assertEqual(calls, [])

    def test_recovery_with_real_trickled_history_and_queue_stops_once(self):
        f = lifecycle_fixtures.LifecycleTests('run')
        f.setUp()
        try:
            f.transport.history = 'missing'
            f.submit(f.runner())
            f.expire()
            responses = {
                '/history/synthetic-owned-prompt': {'body': b'{"unrelated":{}}', 'body_byte_delay': .06},
                '/queue': {'body': b'{"queue_running":[],"queue_pending":[]}', 'body_byte_delay': .06}}
            observer = RecoveryObserver(f.p, f.targets, f.backends)
            original_container = f.observer.container

            def container(service, deadline):
                remaining(deadline)
                return original_container(service, deadline)

            with local_http(responses, concurrent=True) as (url, calls), \
                 patch.object(f.backend, 'transport', HTTPTransport(url)), \
                 patch.object(f.observer, 'queues', side_effect=observer.queues), \
                 patch.object(f.observer, 'container', side_effect=container):
                f.tick()
            f.tick(2)
            self.assertEqual(f.observer.actions, ['start', 'TERM'])
            self.assertEqual(f.gate.snapshot()['owners'], {})
            self.assertFalse(f.observer.current['running'])
            self.assertEqual(f.store.job('job')['state'], 'failed')
            self.assertEqual(f.transport.submissions, 1)
            self.assertEqual(len(f.gate.snapshot()['recoveries']), 1)
            self.assertEqual(len(f.gate.snapshot()['recovery_budget']), 1)
            self.assertEqual(calls, [('GET', '/history/synthetic-owned-prompt'), ('GET', '/queue')])
        finally:
            try:
                f.tearDown()
            finally:
                f.doCleanups()


@unittest.skipUnless(shutil.which('openssl'), 'TLS fixture generation requires local openssl')
class HTTPSReadDeadlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='offline-deadline-tls-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.cert = str(Path(cls.temp.name) / 'localhost-cert.pem')
        key = str(Path(cls.temp.name) / 'ephemeral-test-key.pem')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost',
                        '-keyout', key, '-out', cls.cert], check=True, capture_output=True, timeout=10)
        cls.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.context.load_cert_chain(cls.cert, key)

    def trusted_context(self):
        return ssl.create_default_context(cafile=self.cert)

    def test_https_certificate_verification_is_preserved(self):
        with local_http({'/queue': {}}, self.context) as (url, calls):
            with self.assertRaises(Uncertain):
                HTTPTransport(url).json('GET', '/queue', timeout=1)
            self.assertEqual(calls, [])

    def test_trusted_https_normal_read_completes(self):
        with local_http({'/queue': {}}, self.context) as (url, calls), \
             patch('ssl._create_default_https_context', side_effect=self.trusted_context):
            self.assertEqual(HTTPTransport(url).json('GET', '/queue', timeout=1), {})
            self.assertEqual(calls, [('GET', '/queue')])

    def test_trusted_https_trickled_headers_and_body_are_bounded(self):
        for spec in ({'header_byte_delay': .025},
                     {'body': b'{"queue_running":[],"queue_pending":[]}', 'body_byte_delay': .025}):
            with self.subTest(spec=spec), local_http({'/queue': spec}, self.context) as (url, calls), \
                 patch('ssl._create_default_https_context', side_effect=self.trusted_context):
                started = time.monotonic()
                with self.assertRaises(Uncertain):
                    HTTPTransport(url).json('GET', '/queue', timeout=.2)
                self.assertLess(time.monotonic() - started, .65)
                self.assertEqual(calls, [('GET', '/queue')])


if __name__ == '__main__':
    unittest.main()
