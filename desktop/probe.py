"""Synthetic D0 service. No SQLite, .env, provider or application imports."""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {
                "instance": os.environ["TRAVELFLOW_PROBE_INSTANCE"],
                "pid": os.getpid(),
                "descendant_pid": descendant.pid,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    # A real descendant is deliberately included to verify tree cleanup.
    descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
