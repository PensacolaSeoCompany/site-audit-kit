"""Static test server for tests/test_install_audit.sh (python http.server with two switches).

  python smoke_server.py <dir> <host> <port> [--forbid /path]... [--disallow /path]...

--forbid /x     answer 403 Forbidden for /x (a page the site refuses to show a crawler)
--disallow /x   serve a robots.txt that disallows /x for every user agent (overrides the file)
Logs requests to stderr. Stops on SIGTERM/SIGINT.
"""
import functools
import http.server
import sys

args = sys.argv[1:]
root, host, port = args[0], args[1], int(args[2])
forbid, disallow = set(), []
i = 3
while i < len(args):
    if args[i] == "--forbid":
        forbid.add(args[i + 1])
    elif args[i] == "--disallow":
        disallow.append(args[i + 1])
    else:
        sys.exit(f"unknown option {args[i]}")
    i += 2


class Handler(http.server.SimpleHTTPRequestHandler):
    def _special(self):
        path = self.path.split("?", 1)[0]
        if path in forbid:
            self.send_error(403, "Forbidden")
            return True
        if path == "/robots.txt" and disallow:
            body = ("User-agent: *\n" + "".join(f"Disallow: {p}\n" for p in disallow)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return True
        return False

    def do_GET(self):
        if not self._special():
            super().do_GET()

    def do_HEAD(self):
        if not self._special():
            super().do_HEAD()


http.server.ThreadingHTTPServer((host, port), functools.partial(Handler, directory=root)).serve_forever()
