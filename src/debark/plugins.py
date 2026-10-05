"""Explicitly enabled, local Python hooks for trusted DebArk administrators."""

from __future__ import annotations

import importlib.util
import json
import os
import pwd
import re
import stat
from typing import Any


def _expected_owner(cfg: Any) -> int:
    if cfg.mode == "system":
        return 0
    if os.geteuid() == 0 and os.environ.get("SUDO_USER"):
        try:
            return pwd.getpwnam(os.environ["SUDO_USER"]).pw_uid
        except KeyError:
            pass
    return os.getuid()


def enabled_plugins(cfg: Any) -> list[str]:
    try:
        from .engine import read_regular_text
        value = json.loads(read_regular_text(cfg.cfg_file))
    except (OSError, ValueError, RuntimeError):
        return []
    names = value.get("enabled_plugins", []) if isinstance(value, dict) else []
    return sorted({name for name in names if isinstance(name, str) and
                   re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", name)})


def set_plugin_enabled(cfg: Any, name: str, enabled: bool) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", name):
        raise ValueError("Invalid plugin name.")
    try:
        from .engine import read_regular_text
        value = json.loads(read_regular_text(cfg.cfg_file))
    except (OSError, ValueError, RuntimeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    current = set(enabled_plugins(cfg))
    if enabled:
        current.add(name)
    else:
        current.discard(name)
    value["enabled_plugins"] = sorted(current)
    cfg.save({"enabled_plugins": sorted(current)})


def run_hook(cfg: Any, hook: str, context: dict[str, Any]) -> list[str]:
    directory = cfg.cfg_dir / "plugins"
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        return ["Plugin directory is not a safe directory."]
    owner = _expected_owner(cfg)
    try:
        dir_stat = directory.stat()
    except OSError as exc:
        return [f"Cannot inspect plugin directory: {exc}"]
    if dir_stat.st_uid != owner or dir_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return ["Plugin directory has unsafe ownership or permissions."]
    errors: list[str] = []
    for name in enabled_plugins(cfg):
        path = directory / f"{name}.py"
        if not path.is_file() or path.is_symlink():
            errors.append(f"Enabled plugin is missing or not a regular file: {name}")
            continue
        try:
            metadata = path.stat()
            if metadata.st_uid != owner or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                errors.append(f"Plugin has unsafe ownership or permissions: {name}")
                continue
            spec = importlib.util.spec_from_file_location(f"debark_plugin_{name}", path)
            if spec is None or spec.loader is None:
                errors.append(f"Could not load plugin: {name}")
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            function = getattr(module, hook, None)
            if callable(function) and function(dict(context)) is False:
                errors.append(f"Plugin {name} denied the {hook[3:].replace('_', ' ')} operation.")
        except Exception as exc:
            errors.append(f"Plugin {name} failed during {hook}: {exc}")
    return errors
