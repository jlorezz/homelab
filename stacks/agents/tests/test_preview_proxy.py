import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is needed for the preview ingress integration check')
class PreviewProxyTests(unittest.TestCase):
    def setUp(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get('Upgrade', '').lower() == 'websocket':
                    self.send_response(101)
                    self.send_header('Upgrade', 'websocket')
                    self.send_header('Connection', 'Upgrade')
                    self.end_headers()
                    self.wfile.write(self.path.encode()+b'\n')
                    self.wfile.flush()
                    self.wfile.write(self.rfile.read(4))
                    self.wfile.flush()
                    return
                body=json.dumps({'path':self.path,'host':self.headers['Host'],
                    'scheme':self.headers['X-Forwarded-Proto'],
                    'identity':self.headers.get('Tailscale-User-Login')}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args): pass
        self.upstream=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=self.upstream.serve_forever,daemon=True).start()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        base=f'http://127.0.0.1:{self.upstream.server_port}'
        config={key:base for key in ['dashboard','api','supabase','mail']}
        config.update(port=self.port,origin='https://nas.example.ts.net:4101')
        script=Path(__file__).parents[1]/'previews/proxy.mjs'
        self.process=subprocess.Popen(['node',str(script)],env={**os.environ,'NAS_PREVIEW_PROXY':json.dumps(config)},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1',self.port),timeout=.1):break
            except OSError:time.sleep(.02)
        else:self.fail('Proxy did not start')

    def tearDown(self):
        self.process.terminate();self.process.communicate(timeout=5)
        self.upstream.shutdown();self.upstream.server_close()

    def request(self,path):
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        connection.request('GET',path,headers={'Host':'untrusted.example','Tailscale-User-Login':'forged@example.invalid'})
        response=connection.getresponse();body=response.read();connection.close()
        return response.status,body

    def test_routes_preserve_query_and_use_the_correct_prefix(self):
        for incoming,expected in [('/de/login','/de/login'),('/graphql?operation=test','/graphql?operation=test'),('/supabase/auth/v1/health','/auth/v1/health'),('/mail/api/v1/messages','/mail/api/v1/messages')]:
            with self.subTest(incoming=incoming):
                status,body=self.request(incoming);self.assertEqual(status,200)
                payload=json.loads(body);self.assertEqual(payload['path'],expected)
                self.assertEqual(payload['host'],'nas.example.ts.net:4101')
                self.assertEqual(payload['scheme'],'https');self.assertIsNone(payload['identity'])

    def test_stopped_upstream_returns_explicit_bad_gateway(self):
        self.upstream.shutdown();self.upstream.server_close()
        status,body=self.request('/de/login')
        self.assertEqual(status,502);self.assertIn(b'starting or stopped',body)

    def test_upgrade_tunnels_both_directions_with_realtime_prefix(self):
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as connection:
            connection.sendall(b'GET /supabase/realtime/v1/websocket?vsn=1.0.0 HTTP/1.1\r\nHost: ignored\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n')
            response = connection.makefile('rb')
            self.assertIn(b'101', response.readline())
            while response.readline() != b'\r\n':
                pass
            self.assertEqual(response.readline(), b'/realtime/v1/websocket?vsn=1.0.0\n')
            connection.sendall(b'ping')
            self.assertEqual(response.read(4), b'ping')
            response.close()
