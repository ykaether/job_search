from http.server import BaseHTTPRequestHandler, HTTPServer
import os

PORTFOLIO_URL = "https://ykaether.github.io/ai-portfolio/"

class RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", PORTFOLIO_URL)
        self.end_headers()

    def do_HEAD(self):
        self.send_response(302)
        self.send_header("Location", PORTFOLIO_URL)
        self.end_headers()

    def log_message(self, format, *args):
        return

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), RedirectHandler)
    server.serve_forever()
