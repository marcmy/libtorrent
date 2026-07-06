#!/usr/bin/env python3

import sys
import os
import ssl
import gzip
import base64
import socket
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from http.server import HTTPServer, BaseHTTPRequestHandler

chunked_encoding = False
keepalive = True
expected_host = ''
test_root = Path.cwd().resolve()

try:
    fin = open('test_file', 'rb')
    f = gzip.open('test_file.gz', 'wb')
    f.writelines(fin)
    f.close()
    fin.close()
except Exception:
    pass


def resolve_test_file(request_target):
    """Resolve a request to an existing file directly under the test directory."""
    name = urlsplit(request_target).path.removeprefix('/')
    if not name or name in ('.', '..') or '/' in name or '\\' in name:
        raise FileNotFoundError(name)

    # Select from paths discovered from the test directory rather than joining
    # an attacker-controlled request path to the filesystem root.
    files = {entry.name: entry for entry in test_root.iterdir() if entry.is_file()}
    try:
        return files[name]
    except KeyError as exc:
        raise FileNotFoundError(name) from exc


class http_server_with_timeout(HTTPServer):
    allow_reuse_address = True
    timeout = 250

    def handle_timeout(self):
        print('TIMEOUT')
        raise Exception('timeout')


class http_handler(BaseHTTPRequestHandler):

    def normalize_request_path(self):
        # If the request contains the hostname and port, keep only its path and
        # query. This also handles http:// and https:// without hard-coded
        # scheme lengths.
        parsed = urlsplit(self.path)
        if parsed.scheme in ('http', 'https'):
            self.path = parsed.path or '/'
            if parsed.query:
                self.path += '?' + parsed.query

    def do_GET(self):
        try:
            self.inner_do_GET()
        except Exception:
            print('EXCEPTION')
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()

    def inner_do_GET(self):

        print('INCOMING-REQUEST [from: {}]: {}'.format(self.request.getsockname(), self.requestline))
        print(self.headers)
        sys.stdout.flush()

        global chunked_encoding
        global keepalive
        global expected_host

        if expected_host and self.headers.get('Host') != expected_host:
            print('UNEXPECTED-HOST expected={} actual={}'.format(
                expected_host, self.headers.get('Host')))
            sys.stdout.flush()
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return

        self.normalize_request_path()
        sys.stdout.flush()

        if self.path == '/password_protected':
            passed = False
            if 'Authorization' in self.headers:
                auth = self.headers['Authorization']
                passed = auth == 'Basic %s' % base64.b64encode(b'testuser:testpass').decode()

            if not passed:
                self.send_response(401)
                self.send_header("Connection", "close")
                self.end_headers()
                return

            self.path = '/test_file'

        if self.path == '/redirect':
            self.send_response(301)
            self.send_header("Location", "/test_file")
            self.send_header("Connection", "close")
            self.end_headers()
        elif self.path == '/infinite_redirect':
            self.send_response(301)
            self.send_header("Location", "/infinite_redirect")
            self.send_header("Connection", "close")
            self.end_headers()
        elif self.path == '/relative/redirect':
            self.send_response(301)
            self.send_header("Location", "../test_file")
            self.send_header("Connection", "close")
            self.end_headers()
        elif self.path == '/redirect_auth':
            # same-origin redirect. The client should keep the
            # Authorization header when following.
            self.send_response(301)
            self.send_header("Location", "/password_protected")
            self.send_header("Connection", "close")
            self.end_headers()
        elif self.path == '/redirect_auth_external':
            # cross-origin redirect (different host string: localhost vs
            # 127.0.0.1). The client must NOT forward the Authorization
            # header to the new origin.
            scheme = 'https' if use_ssl else 'http'
            self.send_response(301)
            self.send_header("Location",
                "%s://localhost:%d/password_protected" % (scheme, port))
            self.send_header("Connection", "close")
            self.end_headers()
        elif self.path.startswith('/announce'):
            self.send_response(200)
            response = b'd8:intervali1800e8:completei1e10:incompletei1e' + \
                b'12:min intervali' + min_interval.encode() + b'e' + \
                b'5:peers12:AAAABBCCCCDD' + \
                b'6:peers618:EEEEEEEEEEEEEEEEFF' + \
                b'e'
            self.send_header("Content-Length", "%d" % len(response))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(response)
            self.request.close()
        else:
            filename = ''
            f = None
            try:
                file_path = resolve_test_file(self.path)
                filename = str(file_path)
                f = file_path.open('rb')
                size = int(file_path.stat().st_size)
                start_range = 0
                end_range = size
                if 'Range' in self.headers:
                    self.send_response(206)
                    st, e = self.headers['range'][6:].split('-', 1)
                    sl = len(st)
                    el = len(e)
                    if sl > 0:
                        start_range = int(st)
                        if el > 0:
                            end_range = int(e) + 1
                    elif el > 0:
                        ei = int(e)
                        if ei < size:
                            start_range = size - ei
                    self.send_header('Content-Range', 'bytes ' + str(start_range)
                                  + '-' + str(end_range - 1) + '/' + str(size))
                else:
                    self.send_response(200)
                self.send_header('Accept-Ranges', 'bytes')
                if chunked_encoding:
                    self.send_header('Transfer-Encoding', 'chunked')
                self.send_header('Content-Length', end_range - start_range)
                if filename.endswith('.gz'):
                    self.send_header('Content-Encoding', 'gzip')
                if not keepalive:
                    self.send_header("Connection", "close")

                self.end_headers()

                f.seek(start_range)
                length = end_range - start_range
                while length > 0:
                    to_send = min(length, 0x900)
                    if chunked_encoding:
                        self.wfile.write(b'%x\r\n' % to_send)
                    data = f.read(to_send)
                    print('read %d bytes' % to_send)
                    sys.stdout.flush()
                    self.wfile.write(data)
                    if chunked_encoding:
                        self.wfile.write(b'\r\n')
                    length -= to_send
                    print('sent %d bytes (%d bytes left)' % (len(data), length))
                    sys.stdout.flush()
                if chunked_encoding:
                    self.wfile.write(b'0\r\n\r\n')
            except Exception as e:
                print('FILE ERROR: ', filename, e)
                traceback.print_exc(file=sys.stdout)
                sys.stdout.flush()
                self.send_response(404)
                self.send_header("Content-Length", "0")
                try:
                    self.end_headers()
                except Exception:
                    pass
            finally:
                if f is not None:
                    f.close()
            if not keepalive and not use_ssl:
                try:
                    self.request.shutdown(socket.SHUT_RD)
                except Exception:
                    pass

        print("...DONE")
        sys.stdout.flush()
        self.wfile.flush()

    def do_POST(self):
        try:
            self.inner_do_POST()
        except Exception:
            print('EXCEPTION')
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()

    def inner_do_POST(self):
        print('INCOMING-REQUEST [from: {}]: {}'.format(self.request.getsockname(), self.requestline))
        print(self.headers)
        sys.stdout.flush()

        global keepalive

        self.normalize_request_path()

        content_length = self.headers.get('Content-Length')
        if content_length is not None:
            self.rfile.read(int(content_length))

        if self.path not in ('/wipconn', '/WANIPConnection'):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        filename = ''
        try:
            file_path = resolve_test_file(self.path)
            filename = str(file_path)
            data = file_path.read_bytes()

            self.send_response(200)
            self.send_header('Content-Type', 'text/xml; charset="utf-8"')
            self.send_header('Content-Length', "%d" % len(data))
            if not keepalive:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            print('FILE ERROR: ', filename, e)
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
            self.send_response(404)
            self.send_header("Content-Length", "0")
            try:
                self.end_headers()
            except Exception:
                pass

        print("...DONE")
        sys.stdout.flush()
        self.wfile.flush()


if __name__ == '__main__':
    port = int(sys.argv[1])
    chunked_encoding = sys.argv[2] != '0'
    use_ssl = sys.argv[3] != '0'
    keepalive = sys.argv[4] != '0'
    min_interval = sys.argv[5]
    expected_host = sys.argv[6] if len(sys.argv) > 6 else ''
    print('python version: %s' % sys.version_info.__str__())

    http_handler.protocol_version = 'HTTP/1.1'
    httpd = http_server_with_timeout(('127.0.0.1', port), http_handler)
    if use_ssl:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain("../ssl/server.pem")
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    while True:
        httpd.handle_request()
