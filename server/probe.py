"""对某个模型角色做**一次最小调用**，回答「这条端点现在到底能不能用」。

它回答的是**接线**问题（`base_url` 到不到、密钥收不收、模型名认不认、协议合不合），
**不是**「够不够格上岗」。两件事要分清，否则会把一次 ping 当成验收：

1. **不验视觉能力**：这里只发一句纯文本。视觉探针要真照片，在
   `docs/model-endpoints.md` 那份清单里。
2. **不验质量**：能回答 ≠ 读得对。「换模型就要重跑验收」这条**不受它影响**。
3. **不进任何自动流程**：它要联网、要花额度，**只能由人显式触发**。

为什么需要它：免费／转发类端点会限流、会消失、上游还会悄悄换后端。真正调用时才知道
（判定失败是 502、而且**不留记录**）——那是刻意的设计，但"我现在就要把它配好"
需要更早的反馈。所以自检是**显式的一次动作**，并且它**照常留一条 run**（可审计：
你能看到自己什么时候探过、用的是谁）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import config as config_module
from .model_client import ModelUnavailable, chat
from .paths import default_runs_dir

#: 只回一个字：自检关心的是"这条线通不通"，不是内容。
PROBE_PROMPT = "只回一个字：好"


def probe(role: str, *, env=None, transport=None, sleep=None, clock=None,
          runs_dir: Path | str | None = None, settings: dict | None = None) -> dict:
    """探一次 `role` 的端点。**永远返回一份读数**，不抛异常（CLI 与 HTTP 都要能照原样显示）。

    `settings` 是设置文件那三层里的第一层（`server/settings.py` 读出来的那份）；
    不给就只看环境变量与预设——与真正调用时那一套**同一条解析**（`load_role_config`）。
    """
    started = time.time()
    readout = {
        "role": role,
        "ok": False,
        "provider": None,
        "model": None,
        "base_url": None,
        "key_env": None,
        "latency_ms": None,
        "reply": None,
        "run_id": None,
        "error": None,
    }
    try:
        role_config = config_module.load_role_config(role, env, settings=settings)
    except ValueError as exc:
        readout["error"] = {"code": "model_unavailable", "message": str(exc),
                            "hint": "端点／密钥／模型这三样里缺一样就装不起来；设置页里能看到缺哪个"}
        return readout

    readout.update({
        "provider": role_config.provider,
        "model": role_config.model,
        "base_url": role_config.base_url,
        # 密钥的**变量名**可以显示（值是秘密，名字不是——界面要告诉人往哪填）
        "key_env": (role_config.key_env if not getattr(role_config, "api_key", None) else None),
    })

    try:
        call = chat(role_config, [{"role": "user", "content": PROBE_PROMPT}],
                    tag=f"probe-{role}", runs_dir=runs_dir or default_runs_dir(),
                    transport=transport, sleep=sleep or time.sleep, env=env, clock=clock)
    except ModelUnavailable as exc:
        # 「我们没能问成」：把服务原话（或本地那条缺密钥的话）原样给出来，不另编一句
        readout["error"] = {"code": "model_unavailable", "message": str(exc),
                            "hint": "先看这句话说的是哪一步：连不上／被拒／模型名不认识。"
                                    "配置对了再谈它读得准不准"}
        return readout

    readout.update({
        "ok": True,
        "reply": (call.text or "").strip()[:80],
        "run_id": call.run_id,
        "latency_ms": int((time.time() - started) * 1000),
    })
    return readout


def main(argv: list[str] | None = None, *, transport=None, env=None) -> int:
    """`python3 -m server.probe <角色>`（角色：extract／segmenter／judge／brief）

    退出码：0 ＝ 通了；1 ＝ 没通（原因印在最后一行）；2 ＝ 用法不对。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("用法：python3 -m server.probe <角色>"
              f"（可选：{', '.join(sorted(config_module.ROLE_DEFAULTS))}）", file=sys.stderr)
        return 2
    role = args[0]
    if role not in config_module.ROLE_DEFAULTS:
        print(f"没有这个角色：{role}；可选：{', '.join(sorted(config_module.ROLE_DEFAULTS))}",
              file=sys.stderr)
        return 2

    readout = probe(role, transport=transport, env=env)
    print(json.dumps(readout, ensure_ascii=False, indent=2))
    if readout["ok"]:
        print(f"\n通了：{readout['provider']} / {readout['model']}"
              f"（{readout['latency_ms']}ms）")
        return 0
    print(f"\n没通：{readout['error']['message']}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
