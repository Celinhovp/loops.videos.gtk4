import os
import sys
import threading
import http.server
import socketserver
import urllib.parse
import json
import gi

gi.require_version('Soup', '3.0')
from gi.repository import Soup

class SPAHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, **kwargs):
        self.spa_directory = os.path.abspath(directory or '.')
        super().__init__(*args, directory=self.spa_directory, **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, HEAD')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.end_headers()

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path != '/oauth-token':
            self.send_error(404)
            return

        try:
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            instance = str(payload.get('instance', '')).strip().strip('/')
            if not instance or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for char in instance):
                raise ValueError('Invalid server address')
            fields = {
                key: str(payload[key])
                for key in ('client_id', 'client_secret', 'redirect_uri', 'grant_type', 'code', 'scope')
                if key in payload
            }
            session = Soup.Session.new()
            session.set_timeout(30)
            soup_version = f'{Soup.MAJOR_VERSION}.{Soup.MINOR_VERSION}.{Soup.MICRO_VERSION}'
            session.set_user_agent(f'Loops/1.0.2 libsoup/{soup_version}')

            multipart = Soup.Multipart.new(Soup.FORM_MIME_TYPE_MULTIPART)
            for key, value in fields.items():
                multipart.append_form_string(key, value)

            request = Soup.Message.new_from_multipart(
                f'https://{instance}/oauth/token',
                multipart,
            )
            response_bytes = session.send_and_read(request, None)
            status = request.get_status()
            body = response_bytes.get_data()
        except Exception as error:
            status = 502
            body = json.dumps({'error': str(error)}).encode('utf-8')

        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Clean path
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path == '/oauth-callback':
            query = urllib.parse.parse_qs(parsed_url.query)
            if query.get('code') or query.get('error'):
                host, port = self.server.server_address
                self.server.oauth_callback_url = f'http://{host}:{port}{self.path}'
            body = b'<!doctype html><title>Loops sign in</title><p>Authorization complete. Return to the Loops app.</p>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed_url.path == '/oauth-callback-status':
            callback_url = getattr(self.server, 'oauth_callback_url', None)
            self.server.oauth_callback_url = None
            body = json.dumps({'url': callback_url}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
            return

        rel_path = parsed_url.path.lstrip('/')
        file_path = os.path.join(self.spa_directory, rel_path)

        # SPA fallback: if file doesn't exist and path does not have an extension, serve index.html
        if not os.path.exists(file_path) and '.' not in os.path.basename(rel_path):
            self.path = '/index.html'

        return super().do_GET()

    def log_message(self, format, *args):
        # Suppress noisy request logging in terminal
        pass


class ReusableTCPServer(socketserver.TCPServer):
    # Reclaim the saved loopback port after a clean close. Without
    # SO_REUSEADDR, TIME_WAIT can force a new origin and split WebKit storage.
    allow_reuse_address = True


class SPAServer:
    def __init__(self, dist_dir: str):
        self.dist_dir = os.path.abspath(dist_dir)
        self.httpd = None
        self.thread = None
        self.port = 0

    def start(self) -> str:
        handler = lambda *args, **kwargs: SPAHTTPRequestHandler(*args, directory=self.dist_dir, **kwargs)
        # Keep a stable origin: WebKit localStorage is scoped by scheme, host,
        # and port, so a random port would make saved login credentials vanish
        # every time the app starts.
        config_dir = os.path.join(
            os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')),
            'loops',
        )
        os.makedirs(config_dir, mode=0o700, exist_ok=True)
        port_file = os.path.join(config_dir, 'webview-port')
        preferred_ports = []
        try:
            with open(port_file, 'r', encoding='ascii') as port_handle:
                saved_port = int(port_handle.read().strip())
                if 1024 <= saved_port <= 65535:
                    preferred_ports.append(saved_port)
        except (OSError, ValueError):
            pass
        preferred_ports.extend(port for port in range(43871, 43881) if port not in preferred_ports)

        last_error = None
        for port in preferred_ports:
            try:
                self.httpd = ReusableTCPServer(('127.0.0.1', port), handler)
                break
            except OSError as error:
                last_error = error
        if self.httpd is None:
            raise RuntimeError(f'Could not bind the local Loops server: {last_error}')

        with open(port_file, 'w', encoding='ascii') as port_handle:
            port_handle.write(str(self.httpd.server_address[1]))
        os.chmod(port_file, 0o600)
        self.httpd.oauth_callback_url = None
        self.port = self.httpd.server_address[1]

        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

        return f'http://127.0.0.1:{self.port}/'

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
