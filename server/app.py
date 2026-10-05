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

from .config import load_env_files, load_judge_config
from .http import Api
from .publicbase import public_base_warning, resolve_public_base

DEFAULT_DATA = Path(__file__).resolve().parent.parent / "data"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def build_server(data_dir: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 clock=None, public_base: str | None = None, inbox: Path | str | None = None,
                 segmenter=None, max_upload_bytes: int | None = None,
                 *, judge=None, runs_dir: Path | str | None = None,
                 config=None) -> ThreadingHTTPServer:
    """把 `Api.handle` 挂到 `http.server` 上。返回一个还没 serve_forever 的服务器。

    对外地址在**绑好端口之后**才算：`--port 0` 时端口是系统给的，先算会算错。
    `judge` / `runs_dir` / `config` 是测试接缝（喂假判定角色，别联网、别写真 `runs/`）；
    `segmenter` 是切分的接缝（#10 接上来的口子，不注入就报「切分不可用」）。
    """
    holder: dict = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ai-note/0.0"
        # HTTP/1.1：浏览器（Docusaurus 的列表页要连打索引与图片）不必每张图重开连接。
        # 代价是每个响应都必须带 Content-Length——`Api.handle` 保证这一点，
        # 而且每个请求的 body 都必须读完，否则下一条请求会从头读起。
        protocol_version = "HTTP/1.1"

        def _declared_length(self) -> int | None:
            raw = self.headers.get("Content-Length")
            if raw is None:
                return None
            try:
                return int(raw)
            except ValueError:
                return None

        def _respond(self, method: str) -> None:
            api = holder["api"]
            declared = self._declared_length()
            # 上限**按路由**取（`Api.body_limit`）：写端点是 64 KiB，上传是 32MB。
            # 在**读 body 之前**判：读一个超限的 body 再拒绝不是拒绝。
            limit = api.body_limit(self.path)
            body = b""
            if declared is not None:
                if declared <= limit:
                    body = self.rfile.read(declared) if declared else b""
                else:
                    # 太大：**不读进内存**，明确拒绝并关连接（HTTP/1.1 不能留下半截 body，
                    # 否则下一条请求会从这半截开始读）。
                    self.close_connection = True
            response = api.handle(method, self.path, body,
                                  content_type=self.headers.get("Content-Type") or "",
                                  declared_length=declared)
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            for name, value in response.headers.items():
                self.send_header(name, value)
            if "Content-Length" not in response.headers:
                self.send_header("Content-Length", str(len(response.body)))
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

        def do_GET(self) -> None:  # noqa: N802（http.server 的命名约定）
            self._respond("GET")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._respond("OPTIONS")

        def do_POST(self) -> None:  # noqa: N802
            self._respond("POST")

        def log_message(self, fmt: str, *args) -> None:  # 别把访问日志吞掉
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # 配置先在**开端口之前**校验：坏配置起不来，就不该先占住一个端口
    # （阈值 NaN/越界、provider 不在白名单 → ValueError，由 main 变成退出码 2）。
    cfg = config or load_judge_config()

    # ThreadingHTTPServer：界面与 curl 会同时打它，串行会互相卡。
    httpd = ThreadingHTTPServer((host, port), Handler)
    bound_host, bound_port = httpd.server_address[:2]
    httpd.api = Api(  # 测试与 main() 都从这里看「服务到底绑在哪、对外地址是什么」
        data_dir, clock=clock, bind_host=bound_host, inbox=inbox, segmenter=segmenter,
        max_upload_bytes=max_upload_bytes,
        public_base=resolve_public_base(bound_host, bound_port, public_base),
        judge=judge, runs_dir=runs_dir, config=cfg,
    )
    holder["api"] = httpd.api
    return httpd


@contextlib.contextmanager
def serve(data_dir: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          clock=None, public_base: str | None = None, inbox: Path | str | None = None,
          segmenter=None, max_upload_bytes: int | None = None,
          *, judge=None, runs_dir: Path | str | None = None, config=None):
    """起服务 → `yield` 基址 → 关掉。`port=0` 让系统挑一个空闲端口（测试用）。"""
    httpd = build_server(data_dir, host, port, clock=clock, public_base=public_base,
                         inbox=inbox, segmenter=segmenter, max_upload_bytes=max_upload_bytes,
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


def load_local_env(root: Path | str | None = None) -> list[Path]:
    """把 `.env.local` 灌进环境：仓库根优先，其次 `~/.env.local`（口径同 proto/slice.py:117-144）。
    只回报用了哪个文件，**绝不打印值**；已存在的环境变量不覆盖。

    ⚠ **必须在 `build_parser()` 之前调用**：`--data`／`--public-base`／`--inbox` 的默认值
    是在建 parser 时从环境变量现算的。先建 parser 再灌环境，等于让 `.env.local` 里写的
    `AI_NOTE_*` **静默失效**——那正是这个项目最怕的一类失败（ADR 0007 第 6 条）。
    """
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    return load_env_files([root / ".env.local", Path.home() / ".env.local"])


def build_parser() -> argparse.ArgumentParser:
    """命令行入口。**每个配置项都有环境变量**：手机那条路上，服务常常是被脚本或
    桌面图标拉起来的，命令行改不了（契约 §1）。"""
    parser = argparse.ArgumentParser(description="错题本后端服务（契约 v0，#13 起收件目录可写）")
    parser.add_argument("--data", default=os.environ.get("AI_NOTE_DATA", str(DEFAULT_DATA)),
                        help="数据目录（默认仓库根的 data/；题卡／资产／索引只读）")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help="监听地址（默认只监听本机）。要让手机连上来用 0.0.0.0，"
                             "同时**必须**给 --public-base")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--public-base", default=os.environ.get("AI_NOTE_PUBLIC_BASE"),
                        help="对外可达地址（手机要打开的链接、页锚点用它拼）。不传则按"
                             "**绑定之后**的 host:port 推导；**不许**从 --host 猜"
                             "（ADR 0007 第 5 条记着这处债）。绑 0.0.0.0 时必须显式给")
    parser.add_argument("--inbox", default=os.environ.get("AI_NOTE_INBOX"),
                        help="收件目录：往这里放一个文件就是录入（ADR 0007 第 4 条）。"
                             "默认是**数据目录**下面的 inbox/——它跟着 --data 走，"
                             "绝不会在你把 --data 指到别处之后还往仓库里的 data/inbox 写。"
                             "不监视（inotify 在同步盘上不可靠），手动等价入口是 "
                             "POST /api/inbox/scan")
    return parser


def main(argv: list[str] | None = None) -> int:
    # 环境先灌、parser 后建：顺序有讲究，见 `load_local_env`。
    for env_file in load_local_env():
        print(f"密钥文件：{env_file}", file=sys.stderr)

    parser = build_parser()
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"数据目录不存在：{data_dir}", file=sys.stderr)
        return 2

    try:
        httpd = build_server(data_dir, args.host, args.port, public_base=args.public_base,
                             inbox=args.inbox)
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
    api = httpd.api  # 绑好端口之后的对外地址就在这里（--port 0 时端口是系统给的）
    print(f"服务在 http://{host}:{port}（数据：{data_dir}）", file=sys.stderr)
    print(f"收件目录：{api.catalog.inbox.dir}"
          f"（把照片放进去；手动扫描：POST /api/inbox/scan）", file=sys.stderr)
    print(f"手机打开这个地址上传：{api.catalog.public_url('/upload')}", file=sys.stderr)
    warning = public_base_warning(api.catalog.bind_host, api.catalog.public_base)
    if warning:
        # 喊出来而不是让人到了手机上才发现打不开（ADR 0007 第 6 条）。
        print(f"⚠ {warning['message']}", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
