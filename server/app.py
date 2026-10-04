"""`server/` 的入口：起一个只监听本机的只读 HTTP 服务。

    python3 -m server.app --data data --host 127.0.0.1 --port 8765

只读、只监听本机（ADR 0003 单用户本地优先）。**处理器里不许 `sys.exit`**
（编排裁决 D1：原型的 `die()` 会杀掉请求）——HTTP 处理器只有 `ApiError` 与
「兜底 500 也走 JSON 信封」两条路，任何异常都由 `Api.handle` 收进信封。
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import load_judge_config
from .http import Api

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_INBOX = DEFAULT_DATA / "inbox"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def build_server(data_dir: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 clock=None, public_base: str | None = None,
                 *, judge=None, runs_dir: Path | str | None = None,
                 config=None) -> ThreadingHTTPServer:
    """把 `Api.handle` 挂到 `http.server` 上。返回一个还没 serve_forever 的服务器。

    对外地址在**绑好端口之后**才算：`--port 0` 时端口是系统给的，先算会算错。
    `judge` / `runs_dir` / `config` 是测试接缝（喂假判定角色，别联网、别写真 `runs/`）。
    """
    holder: dict = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ai-note/0.0"
        # HTTP/1.1：浏览器（Docusaurus 的列表页要连打索引与图片）不必每张图重开连接。
        # 代价是每个响应都必须带 Content-Length——`Api.handle` 保证这一点。
        protocol_version = "HTTP/1.1"

        def _read_body(self) -> bytes:
            """POST 的 body。没有 Content-Length 就当空 body（`AttemptEndpoint` 会 400）。"""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return b""
            return self.rfile.read(length) if length > 0 else b""

        def _respond(self, method: str, body: bytes | None = None) -> None:
            response = holder["api"].handle(method, self.path, body)
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
            self._respond("POST", self._read_body())

        def log_message(self, fmt: str, *args) -> None:  # 别把访问日志吞掉
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # 配置先在**开端口之前**校验：坏配置起不来，就不该先占住一个端口
    # （阈值 NaN/越界、provider 不在白名单 → ValueError，由 main 变成退出码 2）。
    cfg = config or load_judge_config()

    # ThreadingHTTPServer：界面与 curl 会同时打它，串行会互相卡。
    httpd = ThreadingHTTPServer((host, port), Handler)
    bound_host, bound_port = httpd.server_address[:2]
    holder["api"] = Api(data_dir, clock=clock,
                        public_base=public_base or f"http://{bound_host}:{bound_port}",
                        judge=judge, runs_dir=runs_dir, config=cfg)
    return httpd


@contextlib.contextmanager
def serve(data_dir: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          clock=None, public_base: str | None = None,
          *, judge=None, runs_dir: Path | str | None = None, config=None):
    """起服务 → `yield` 基址 → 关掉。`port=0` 让系统挑一个空闲端口（测试用）。"""
    httpd = build_server(data_dir, host, port, clock=clock, public_base=public_base,
                         judge=judge, runs_dir=runs_dir, config=config)
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

    # 密钥来源：仓库根的 .env.local 优先，其次 ~/.env.local（口径同 proto/slice.py:117-144）。
    # 只打印用了哪个文件，**绝不打印值**。已存在的环境变量不覆盖。
    from .config import load_env_files

    root = Path(__file__).resolve().parent.parent
    for env_file in load_env_files([root / ".env.local", Path.home() / ".env.local"]):
        print(f"密钥文件：{env_file}", file=sys.stderr)

    try:
        httpd = build_server(data_dir, args.host, args.port, public_base=args.public_base)
    except ValueError as exc:
        # 坏配置**起不来**（阈值 NaN/越界、provider 不在白名单）——不许静默降级
        print(f"配置有问题，服务不启动：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # 起不来也要说人话。裸回溯（还是 `Errno 98`）等于让人自己去猜哪一步错了，
        # 这与「不许静默」是同一条道理——端口被占是最常见的一种起不来。
        if exc.errno == errno.EADDRINUSE:
            print(f"端口 {args.port} 已被占用：换一个 --port，或先关掉占着它的进程。"
                  f"（想随便挑一个空闲端口就传 --port 0）", file=sys.stderr)
            return 2
        print(f"起不来：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
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
