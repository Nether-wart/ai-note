"""模型设置的读、写与逐字段解析（**唯一实现**，契约 §10.6）。

三层来源，**逐字段**回落：

    `<数据目录>/settings.json`  >  环境变量  >  预设默认

为什么是逐字段而不是整份覆盖：整份覆盖会让「界面上没设过的那一项」凭空盖掉环境变量
（无头部署里 `.env.local` 配好的东西被一个空表单抹掉）；反过来「环境变量永远赢」又会造成
「界面上改了却不生效」——对单用户桌面程序，那一半更常见、也更难查。

为什么**每次调用前重读**这个文件（`role_config`）：改完立刻生效，不必重启服务。
Tauri 里没有 shell 去改环境变量，「重启 sidecar」是额外的一整套生命周期；
而读一个 JSON 的成本可以忽略。

四条不许：

1. **密钥不进任何响应、不进日志**：对外只给 `key_set` 与**末四位**（`key.hint`）。
2. **`api_key` 省略或空串 ＝ 不改动这一项**：设置页「加载一次再保存一次」不得把密钥抹掉——
   这是这类页面最常见的静默数据丢失。要**清掉**得显式给 `null`。
3. **写盘前先校验**：候选设置跑一遍**四个角色**（extract／segmenter／judge／brief）的解析，任何一个装不起来就拒绝、**一个字节不写**。
   「坏配置起不来」这条纪律在**写入口**也要成立。
4. **文件坏了不许拿环境变量悄悄兜底**：读不出来就报 `settings_unreadable`／`settings_malformed`，
   模型调用**明确失败**。悄悄回落会让人以为在用自己配的那一家，其实不是。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import config as config_module

FILENAME = "settings.json"
TOP_KEYS = ("roles", "providers")
KEY_HINT_LEN = 4


class SettingsError(Exception):
    """坏设置：读不出来、形状不对、或值装不起来。

    `code` 走契约 §9 的四档：`settings_unreadable`／`settings_malformed`
    （读的时候，**警告级**）与 `settings_invalid`／`settings_unknown_field`
    （写的时候，**400**）。两类处置不同：前者是"这份文件现在不能用"，
    后者是"你这一次输入不收"。
    """

    def __init__(self, code: str, message: str, hint: str | None = None,
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details


def settings_path(catalog) -> Path:
    """`<数据目录>/settings.json`（ADR 0008 那个目录，不是仓库）。"""
    return Path(catalog.root) / FILENAME


def load_settings(catalog) -> dict:
    """读设置文件。**没有这个文件**不是错误（那就是"三层里只用后两层"），返回 `{}`。

    文件在但读不出来／不是 JSON 对象 → `SettingsError`。**不返回 `{}` 兜底**：
    那等于把"我按你没配过的方式在跑"这件事藏起来。
    """
    path = settings_path(catalog)
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsError(
            "settings_unreadable",
            f"设置文件读不出来：{exc}",
            hint=f"看一眼 {path} 的权限与内容；删掉它就回到「只用环境变量与预设」",
        ) from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SettingsError(
            "settings_malformed",
            f"设置文件不是合法 JSON：{exc}",
            hint=f"修好 {path}（或删掉它，回到只用环境变量与预设）",
        ) from exc
    if not isinstance(data, dict):
        raise SettingsError(
            "settings_malformed",
            f"设置文件必须是一个 JSON 对象，拿到的是 {type(data).__name__}",
            hint=f"顶层只认 {', '.join(TOP_KEYS)}",
        )
    return data


def validate(payload, *, stored: dict | None = None) -> dict:
    """校验一次写入的请求体，返回**要落盘的那份**。

    `stored` 是当前盘上的那份：空串／省略的 `api_key` 从它那里取回来（规矩 2）。
    """
    if not isinstance(payload, dict):
        raise SettingsError("settings_unknown_field",
                            f"设置必须是一个 JSON 对象，拿到的是 {type(payload).__name__}")
    unknown = [key for key in payload if key not in TOP_KEYS]
    if unknown:
        raise SettingsError(
            "settings_unknown_field",
            f"认不出的顶层字段：{', '.join(sorted(unknown))}",
            hint=f"顶层只认 {', '.join(TOP_KEYS)}", details={"unknown": sorted(unknown)},
        )

    stored = stored or {}
    roles_in = payload.get("roles", {}) or {}
    providers_in = payload.get("providers", {}) or {}
    if not isinstance(roles_in, dict) or not isinstance(providers_in, dict):
        raise SettingsError("settings_unknown_field", "`roles` 与 `providers` 都必须是对象")

    known_roles = sorted(config_module.ROLE_DEFAULTS)
    bad_roles = [role for role in roles_in if role not in config_module.ROLE_DEFAULTS]
    if bad_roles:
        raise SettingsError(
            "settings_unknown_field",
            f"认不出的角色：{', '.join(sorted(bad_roles))}",
            hint=f"角色表：{', '.join(known_roles)}", details={"unknown": sorted(bad_roles)},
        )

    roles = {}
    for role, spec in roles_in.items():
        if not isinstance(spec, dict):
            raise SettingsError("settings_unknown_field", f"角色 {role} 的设置必须是对象")
        clean = {}
        for field in ("provider", "model"):
            if field not in spec:
                continue
            value = spec[field]
            if value is None:
                continue                      # 显式 null ＝ 不设这一项（回落下一层）
            if not isinstance(value, str) or not value.strip():
                raise SettingsError(
                    "settings_unknown_field",
                    f"{role}.{field} 得是非空字符串或 null",
                    hint="要「不设这一项」就给 null（或干脆省略这个键）",
                    details={"role": role, "field": field, "value": repr(value)[:60]},
                )
            clean[field] = value.strip()
        if clean:
            roles[role] = clean

    stored_providers = (stored.get("providers") or {})
    providers = {}
    for name, spec in providers_in.items():
        if not isinstance(name, str) or not name.strip():
            raise SettingsError("settings_unknown_field", "provider 名得是非空字符串")
        if not isinstance(spec, dict):
            raise SettingsError("settings_unknown_field", f"provider {name} 的设置必须是对象")
        unknown_fields = [key for key in spec if key not in ("base_url", "api_key", "model")]
        if unknown_fields:
            raise SettingsError(
                "settings_unknown_field",
                f"provider {name} 上有认不出的字段：{', '.join(sorted(unknown_fields))}",
                hint="只认 base_url／api_key／model", details={"provider": name,
                                                              "unknown": sorted(unknown_fields)},
            )
        clean = {}
        for field in ("base_url", "model"):
            value = spec.get(field)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise SettingsError(
                    "settings_unknown_field",
                    f"{name}.{field} 得是非空字符串或 null",
                    details={"provider": name, "field": field},
                )
            clean[field] = value.strip()
        # 规矩 2：省略／空串＝**不改动**；只有显式 null 才是清掉。
        if "api_key" not in spec:
            carried = (stored_providers.get(name) or {}).get("api_key")
            if carried:
                clean["api_key"] = carried
        else:
            value = spec["api_key"]
            if value is None:
                pass                                   # 显式清掉
            elif not isinstance(value, str):
                raise SettingsError("settings_unknown_field",
                                    f"{name}.api_key 得是字符串或 null",
                                    details={"provider": name})
            elif value.strip():
                clean["api_key"] = value.strip()
            else:
                carried = (stored_providers.get(name) or {}).get("api_key")
                if carried:
                    clean["api_key"] = carried          # 空串＝不改动（不是清掉）
        if clean:
            providers[name] = clean

    return {"roles": roles, "providers": providers}


def check(payload: dict, *, env=None) -> None:
    """拿候选设置把**四个角色**都解析一遍。装不起来 → `settings_invalid`。

    这是「坏配置起不来」在**写入口**的那一份：一个字节都还没写就已经知道它装不起来。
    """
    try:
        for role in sorted(config_module.ROLE_DEFAULTS):
            config_module.load_role_config(role, env, settings=payload)
    except ValueError as exc:
        raise SettingsError(
            "settings_invalid",
            str(exc),
            hint="先补齐缺的那一项（端点／密钥／模型）再保存；这次一个字节都没写",
        ) from exc


def save_settings(catalog, payload, *, env=None) -> Path:
    """校验 → 解析一遍 → **原子写**。任何一步不过，盘上那份原封不动。"""
    path = settings_path(catalog)
    try:
        stored = load_settings(catalog)
    except SettingsError:
        # 盘上那份已经坏了：这次写入**就是来修它的**，所以按"没有旧值"处理，
        # 但仍然要求这次写进去的东西自己能装起来（下面 check 会验）。
        stored = {}
    clean = validate(payload, stored=stored)
    check(clean, env=env)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def resolve(role: str, env, settings: dict) -> dict:
    """一个角色的**生效值** + 每一项**来自哪一层**（界面要靠它标出「这项来自 .env.local」）。

    只在设置文件里找**用户真设过的字段**，其余回落——`source` 就是这句话的读数。
    """
    default = config_module.ROLE_DEFAULTS[role]
    prefix = role.upper()
    mine = (settings.get("roles") or {}).get(role) or {}
    providers = settings.get("providers") or {}

    provider = mine.get("provider") or (env.get(f"{prefix}_PROVIDER") or "").strip() \
        or default["provider"]
    provider_source = "settings" if mine.get("provider") else \
        ("env" if (env.get(f"{prefix}_PROVIDER") or "").strip() else "default")

    # provider 的端点：设置文件里定义过就用它，否则走 config.resolve_provider（预设／环境）。
    definition = providers.get(provider) or {}
    try:
        merged = config_module.resolve_provider(provider, env, prefix=prefix,
                                                providers=providers)
    except ValueError as exc:
        return {
            "role": role,
            "error": {"code": "settings_invalid", "message": str(exc)},
            "provider": {"value": provider, "source": provider_source},
        }
    base_url = definition.get("base_url") or merged["base_url"]
    base_source = "settings" if definition.get("base_url") else \
        ("env" if provider not in config_module.PROVIDERS else "default")

    preset_model = definition.get("model") or merged.get("default_model") or ""
    fallback = default["model"] if provider == default["provider"] else preset_model
    model = mine.get("model") or (env.get(f"{prefix}_MODEL") or "").strip() or fallback
    model_source = "settings" if mine.get("model") else \
        ("env" if (env.get(f"{prefix}_MODEL") or "").strip() else "default")

    key_env = merged.get("key_env") or f"AI_NOTE_PROVIDER_{provider.upper()}_API_KEY"
    file_key = (definition.get("api_key") or "").strip()
    env_key = (env.get(key_env) or "").strip()
    api_key = file_key or env_key
    key_source = "settings" if file_key else ("env" if env_key else "default")

    return {
        "role": role,
        "provider": {"value": provider, "source": provider_source},
        "model": {"value": model, "source": model_source},
        "base_url": {"value": base_url, "source": base_source},
        "key": {"set": bool(api_key), "hint": _hint(api_key), "source": key_source},
        "key_env": key_env,
    }


def _hint(api_key: str) -> str | None:
    """只给**末四位**。整把钥匙永不进任何响应（契约 §10.6）。"""
    if not api_key:
        return None
    return "…" + api_key[-KEY_HINT_LEN:] if len(api_key) > KEY_HINT_LEN else "…" + api_key


def public_view(catalog, env=None) -> tuple[dict, list]:
    """`GET /api/settings` 的 `(data, warnings)`。

    **警告走信封**（与词表那几条同一个口径）：文件读不出来时仍然 200，
    `data.file_ok` 是 `false`、原因在 `warnings[]` 里——页面要能画出来告诉人哪里坏了。
    密钥只以 `key_set`／`hint` 出现，整把钥匙永不进任何响应。
    """
    env = os.environ if env is None else env
    env = dict(env)
    path = settings_path(catalog)
    warnings = []
    settings: dict = {}
    file_ok = True
    try:
        settings = load_settings(catalog)
    except SettingsError as exc:
        # 读不出来**仍然 200**（页面要能画出来告诉人哪里坏了），但明说，且下面
        # 的角色解析**不拿它兜底**——`role_config` 那条路会明确失败（规矩 4）。
        file_ok = False
        # §2 的 `Warning` 只有四个键，`id` 装的是**卡 id**——路径不是 id，
        # 所以路径进 `message`（`id` 留 `null`）。形状不许自创第五个键。
        warnings.append({
            "code": exc.code, "level": "warning", "id": None,
            "message": f"{exc.message}（{path}）",
        })

    roles = {role: resolve(role, env, settings) for role in sorted(config_module.ROLE_DEFAULTS)}
    providers = {}
    for name, preset in config_module.PROVIDERS.items():
        providers[name] = {
            "preset": True, "base_url": preset["base_url"],
            "model": preset["default_model"], "key_env": preset["key_env"],
            "key": {"set": bool((env.get(preset["key_env"]) or "").strip()),
                    "hint": _hint((env.get(preset["key_env"]) or "").strip()),
                    # 预设的密钥**只能**来自环境变量；没设就是 default（不是"来自 env 的空值"）
                    "source": ("env" if (env.get(preset["key_env"]) or "").strip() else "default")},
        }
    for name, definition in (settings.get("providers") or {}).items():
        if name in providers:
            continue
        api_key = (definition.get("api_key") or "").strip()
        providers[name] = {
            "preset": False, "base_url": definition.get("base_url"),
            "model": definition.get("model"), "key_env": None,
            "key": {"set": bool(api_key), "hint": _hint(api_key), "source": "settings"},
        }
    return {
        "file": str(path),
        "file_ok": file_ok,
        "exists": path.exists(),
        "roles": roles,
        "providers": providers,
        "applies": "立刻生效：服务每次模型调用前重读这个文件",
    }, warnings


def role_config(role: str, catalog, env=None):
    """**立刻生效的那一条路**：每次调用都重读设置文件，再走 `config.load_role_config`。

    模型客户端走这里，而不是启动时读一次——契约 §10.6 的口径。
    设置文件坏了就把 `SettingsError` 抛出去（**不回落**，见模块开头的规矩 4）。
    """
    env = os.environ if env is None else env
    return config_module.load_role_config(role, env, settings=load_settings(catalog))
