"""`server/` 的入口：起一个只监听本机的只读 HTTP 服务。

    python3 -m server.app --data data --host 127.0.0.1 --port 8765

只读、只监听本机（ADR 0003 单用户本地优先）。**处理器里不许 `sys.exit`**
（编排裁决 D1：原型的 `die()` 会杀掉请求）——HTTP 处理器只有 `ApiError` 与
「兜底 500 也走 JSON 信封」两条路，任何异常都由 `Api.handle` 收进信封。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .http import Api

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_INBOX = DEFAULT_DATA / "inbox"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def build_server(data_dir: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 clock=None, public_base: str | None = None) -> ThreadingHTTPServer:
    """把 `Api.handle` 挂到 `http.server` 上。返回一个还没 serve_forever 的服务器。

    对外地址在**绑好端口之后**才算：`--port 0` 时端口是系统给的，先算会算错。
    """
    holder: dict = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ai-note/0.0"
        # HTTP/1.1：浏览器（Docusaurus 的列表页要连打索引与图片）不必每张图重开连接。
        # 代价是每个响应都必须带 Content-Length——`Api.handle` 保证这一点。
        protocol_version = "HTTP/1.1"

        def _respond(self, method: str) -> None:
            response = holder["api"].handle(method, self.path)
            body = response.body
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            for name, value in response.headers.items():
                self.send_header(name, value)
            if "Content-Length" not in response.headers:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802（http.server 的命名约定）
            self._respond("GET")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._respond("OPTIONS")

        def do_POST(self) -> None:  # noqa: N802
            self._respond("POST")

        def log_message(self, fmt: str, *args) -> None:  # 别把访问日志吞掉
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ThreadingHTTPServer：界面与 curl 会同时打它，串行会互相卡。
    httpd = ThreadingHTTPServer((host, port), Handler)
    bound_host, bound_port = httpd.server_address[:2]
    holder["api"] = Api(data_dir, clock=clock,
                        public_base=public_base or f"http://{bound_host}:{bound_port}")
    return httpd


@contextlib.contextmanager
def serve(data_dir: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          clock=None, public_base: str | None = None):
    """起服务 → `yield` 基址 → 关掉。`port=0` 让系统挑一个空闲端口（测试用）。"""
    httpd = build_server(data_dir, host, port, clock=clock, public_base=public_base)
    bound_host, bound_port = httpd.server_address[:2]
    base_url = f"http://{bound_host}:{bound_port}"
    import threading

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield base_url
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="错题本只读服务（契约 v0）")
    parser.add_argument("--data", default=os.environ.get("AI_NOTE_DATA", str(DEFAULT_DATA)),
                        help="数据目录（默认仓库根的 data/；只读）")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="监听地址（默认只监听本机）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--public-base", default=os.environ.get("AI_NOTE_PUBLIC_BASE"),
                        help="对外可达地址（手机要打开的链接用它）。不传则按绑定后的"
                             "host:port 推导；**不许**从 --host 猜（ADR 0007 第 5 条）")
    parser.add_argument("--inbox", default=os.environ.get("AI_NOTE_INBOX", str(DEFAULT_INBOX)),
                        help="收件目录：往这里放一个文件就是录入（ADR 0007 第 4 条）。"
                             "v0 只把它报出来，不监视（#13）")
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"数据目录不存在：{data_dir}", file=sys.stderr)
        return 2
    httpd = build_server(data_dir, args.host, args.port, public_base=args.public_base)
    host, port = httpd.server_address[:2]
    print(f"只读服务在 http://{host}:{port}（数据：{data_dir}）", file=sys.stderr)
    print(f"收件目录：{args.inbox}（v0 不监视，#13 接手）", file=sys.stderr)
    if args.public_base:
        print(f"对外地址：{args.public_base}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
