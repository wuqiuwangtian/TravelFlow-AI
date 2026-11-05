"""仅回显虚构P0数据的测试服务，不是TravelFlow后端，不读取任何配置。"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/echo":
            self.send_error(404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024:
                raise ValueError("invalid length")
            data = json.loads(self.rfile.read(size))
            if self.headers.get("X-P0-Token") != "fixture-token":
                raise ValueError("invalid fixture token")
            if data != {"message": "synthetic-p0"}:
                raise ValueError("unexpected synthetic request")
        except (ValueError, UnicodeError):
            self.send_error(400)
            return
        body = b'{"probe":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # 故意不设置CORS：原生HTTP应独立执行，不依赖浏览器代理绕过。
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # 不记录请求体或任何凭据。


if __name__ == "__main__":
    print("P0 synthetic HTTP fixture on 127.0.0.1:18510", flush=True)
    HTTPServer(("127.0.0.1", 18510), Handler).serve_forever()
