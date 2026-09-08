"""
ERPNext Payment Hub - Windows Device Helper

Runs locally on a POS computer and exposes only the machine hostname.
Default endpoint:
    http://127.0.0.1:8765/device

No ERPNext credentials or payment-provider credentials are stored here.
"""

import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer


HOST = "127.0.0.1"
PORT = 8765


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path.rstrip("/") != "/device":
            self._send(404, {"error": "not_found"})
            return

        self._send(
            200,
            {
                "computer_name": socket.gethostname().strip().upper(),
                "service": "erpnext-payment-hub-device-helper",
                "version": "0.1.4",
            },
        )

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    HTTPServer((HOST, PORT), Handler).serve_forever()
