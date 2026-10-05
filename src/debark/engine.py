#!/usr/bin/env python3
"""Install Debian packages on Arch Linux in a per-package application tree."""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import fcntl
import getpass
import hashlib
import io
import json
import os
import platform
import posixpath
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from . import __license__, __maintainer__, __url__, __version__
from . import experimental
from .plugins import enabled_plugins, run_hook, set_plugin_enabled
from .resolver import rank_candidates

VERSION = __version__
AUTHOR = __maintainer__
REPO = __url__
XATTR_PKG = "user.debark.pkg"
XATTR_VERSION = "user.debark.version"
XATTR_INSTALLED = "user.debark.installed"
XATTR_HASH = "user.debark.sha256"
PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,127}$")
COMMAND_NAME = re.compile(r"^[A-Za-z0-9_.+-]+$")
DB_SCHEMA_VERSION = 3
SYSTEM_ACCESS_COMMANDS = frozenset({
    "install", "remove", "list", "search", "files", "verify", "scan", "update",
    "upgrade", "repo", "cve", "config", "repair", "gc", "log", "stats", "snapshot",
    "rollback", "export", "import", "bulk", "watch", "profile", "plugin", "pin",
    "license",
})
COMMAND_NAMES = frozenset({
    "install", "remove", "list", "search", "info", "files", "verify", "scan",
    "update", "upgrade", "config", "doctor", "repo", "cve", "repair", "gc",
    "log", "stats", "snapshot", "rollback", "extract", "convert", "export",
    "import", "bulk", "watch", "profile", "plugin", "pin", "license", "help",
    "about",
})
COMMAND_SHORTCUTS = {
    "-S": ("install",), "-i": ("install",),
    "-R": ("remove",), "-r": ("remove",),
    "-Q": ("list",), "-l": ("list",),
    "-Qs": ("search",), "-Ss": ("repo", "search"),
    "-Qi": ("info",), "-s": ("info",), "-Si": ("repo", "info"),
    "-Ql": ("files",), "-L": ("files",),
    "-Qk": ("verify",), "-V": ("verify",),
    "-Fy": ("update",), "-Qu": ("upgrade",),
}


def normalize_command_shortcuts(argv: list[str]) -> list[str]:
    """Expand common pacman and dpkg operation shortcuts into DebArk commands."""
    normalized = list(argv)
    index = 0
    while index < len(normalized):
        token = normalized[index]
        if token in {"--threads", "--profile"}:
            index += 2
            continue
        if token.startswith(("--threads=", "--profile=")):
            index += 1
            continue
        if token in COMMAND_NAMES:
            return normalized
        shortcut = COMMAND_SHORTCUTS.get(token)
        if shortcut:
            return normalized[:index] + list(shortcut) + normalized[index + 1:]
        index += 1
    return normalized


PROTECTED_ARCH_PACKAGES = {
    "glibc", "gcc-libs", "openssl", "systemd", "systemd-libs", "dbus",
    "util-linux", "libxcrypt", "linux", "linux-lts", "pacman", "filesystem",
}

BUILTIN_DEPMAP = {
    "libc6": "glibc", "libstdc++6": "gcc-libs", "libgcc-s1": "gcc-libs",
    "libgtk-3-0": "gtk3", "libgtk-4-1": "gtk4",
    "libgdk-pixbuf-2.0-0": "gdk-pixbuf2", "libglib2.0-0": "glib2",
    "libpango-1.0-0": "pango", "libpangocairo-1.0-0": "pango",
    "libcairo2": "cairo", "libatk1.0-0": "atk",
    "libatk-bridge2.0-0": "at-spi2-atk", "libatspi2.0-0": "at-spi2-core",
    "gir1.2-atspi-2.0": "at-spi2-core", "libnotify4": "libnotify",
    "libdbus-1-3": "dbus", "libappindicator3-1": "libappindicator-gtk3",
    "libayatana-appindicator3-1": "libayatana-appindicator",
    "libx11-6": "libx11", "libx11-xcb1": "libx11", "libxcb1": "libxcb",
    "libxcb-dri3-0": "libxcb", "libxcomposite1": "libxcomposite",
    "libxdamage1": "libxdamage", "libxext6": "libxext",
    "libxfixes3": "libxfixes", "libxrandr2": "libxrandr",
    "libxrender1": "libxrender", "libxtst6": "libxtst", "libxss1": "libxss",
    "libxi6": "libxi", "libxcursor1": "libxcursor",
    "libxkbcommon0": "libxkbcommon", "libxkbcommon-x11-0": "libxkbcommon-x11",
    "libxshmfence1": "libxshmfence", "libxinerama1": "libxinerama",
    "libdrm2": "libdrm", "libgbm1": "mesa", "libegl1": "libglvnd",
    "libgl1": "libglvnd", "libgles2": "libglvnd", "libglx0": "libglvnd",
    "libglu1-mesa": "glu", "libvulkan1": "vulkan-icd-loader",
    "libnss3": "nss", "libnspr4": "nspr", "libssl3": "openssl",
    "libgnutls30": "gnutls", "libcurl4": "curl",
    "libcurl3-gnutls": "libcurl-gnutls", "libsoup2.4-1": "libsoup",
    "libsoup-3.0-0": "libsoup3", "libsecret-1-0": "libsecret",
    "libuuid1": "util-linux", "libblkid1": "util-linux",
    "libmount1": "util-linux", "libudev1": "systemd-libs",
    "libsystemd0": "systemd-libs", "libpcre2-8-0": "pcre2",
    "libffi8": "libffi", "zlib1g": "zlib", "liblzma5": "xz",
    "libbz2-1.0": "bzip2", "libzstd1": "zstd", "liblz4-1": "lz4",
    "python3": "python", "python3-gi": "python-gobject",
    "python3-cairo": "python-cairo", "python3-dbus": "python-dbus",
    "python3-requests": "python-requests", "python3-yaml": "python-yaml",
    "python3-pip": "python-pip", "python3-setuptools": "python-setuptools",
    "libasound2": "alsa-lib", "libpulse0": "libpulse",
    "libpulse-mainloop-glib0": "libpulse", "libcups2": "libcups",
    "libexpat1": "expat", "libfontconfig1": "fontconfig",
    "libfreetype6": "freetype2", "libharfbuzz0b": "harfbuzz",
    "libjpeg-turbo8": "libjpeg-turbo", "libjpeg8": "libjpeg-turbo",
    "libpng16-16": "libpng", "libtiff5": "libtiff", "libtiff6": "libtiff",
    "libwebp7": "libwebp", "libwebpdemux2": "libwebp", "libwebpmux3": "libwebp",
    "libxml2": "libxml2", "libxslt1.1": "libxslt",
    "libavcodec58": "ffmpeg", "libavformat58": "ffmpeg",
    "libavutil56": "ffmpeg", "libswscale5": "ffmpeg",
    "fonts-liberation": "ttf-liberation", "fonts-dejavu-core": "ttf-dejavu",
    "fonts-noto-cjk": "noto-fonts-cjk", "fonts-noto-color-emoji": "noto-fonts-emoji",
    "adwaita-icon-theme": "adwaita-icon-theme", "hicolor-icon-theme": "hicolor-icon-theme",
    "gnome-themes-extra": "gnome-themes-extra", "xdg-utils": "xdg-utils",
    "bubblewrap": "bubblewrap", "apparmor": "apparmor",
    "apparmor-profiles": "apparmor", "apparmor-utils": "apparmor",
    "ca-certificates": "ca-certificates", "openssl": "openssl",
    "xdotool": "xdotool", "wmctrl": "wmctrl", "libxdo3": "xdotool",
    "unzip": "unzip", "zip": "zip", "curl": "curl", "wget": "wget",
    "git": "git", "libcanberra-gtk3-0": "libcanberra",
    "libcanberra0": "libcanberra", "libdconf1": "dconf",
    "gnome-keyring": "gnome-keyring", "policykit-1": "polkit",
    "libpolkit-gobject-1-0": "polkit", "gvfs": "gvfs",
    "xdg-desktop-portal": "xdg-desktop-portal",
    "xdg-desktop-portal-gtk": "xdg-desktop-portal-gtk",
    "libva2": "libva", "libva-drm2": "libva", "libva-x11-2": "libva",
    "libva-wayland2": "libva", "libvdpau1": "libvdpau",
}

class DebArkError(Exception):
    pass

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def current_user_home() -> Path:
    if os.geteuid() == 0 and os.environ.get("SUDO_USER"):
        try:
            return Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
        except KeyError:
            pass
    return Path.home()

def user_owner() -> tuple[int, int] | None:
    if os.geteuid() != 0 or not os.environ.get("SUDO_USER"):
        return None
    try:
        user = pwd.getpwnam(os.environ["SUDO_USER"])
        return user.pw_uid, user.pw_gid
    except KeyError:
        return None

def validate_directory_chain(path: Path) -> None:
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DebArkError(f"Cannot inspect directory path {current}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise DebArkError(f"Refusing to use a symlinked directory path: {current}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise DebArkError(f"Expected a directory at {current}.")

def read_regular_text(path: Path) -> str:
    validate_directory_chain(path.parent)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(fd)
            raise OSError(f"Not a regular file: {path}")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            return stream.read()
    except OSError:
        raise

def set_user_owner(path: Path, recursive: bool = False) -> None:
    owner = user_owner()
    if owner is None:
        return
    uid, gid = owner
    paths = [path]
    if recursive and path.is_dir() and not path.is_symlink():
        paths = [path, *path.rglob("*")]
    for item in paths:
        with contextlib.suppress(OSError):
            os.chown(item, uid, gid, follow_symlinks=False)

class Config:
    def __init__(self, force_user: bool = False):
        self.mode = "user" if force_user else self._detect_mode()
        self.home_dir = current_user_home() if force_user else Path.home()
        self.set_paths()
        self.auto_yes = False
        self.colors = True
        self.experimental_features = False
        self.threads = 4
        self._load()

    @staticmethod
    def _detect_mode() -> str:
        return "system"

    def set_paths(self) -> None:
        if self.mode == "system":
            self.cfg_dir = Path("/etc/debark")
            self.data_dir = Path("/var/lib/debark")
            self.bin_dir = Path("/usr/local/bin")
            self.apps_root = Path("/opt")
            self.desktop_root = Path("/usr/local/share/applications")
            self.icons_root = Path("/usr/local/share/icons/hicolor")
        else:
            home = self.home_dir
            self.cfg_dir = home / ".config/debark"
            self.data_dir = home / ".local/share/debark"
            self.bin_dir = home / ".local/bin"
            self.apps_root = home / ".local/opt"
            self.desktop_root = home / ".local/share/applications"
            self.icons_root = home / ".local/share/icons/hicolor"
        self.cache_dir = self.data_dir / "cache"
        self.pkgs_dir = self.data_dir / "pkgs"
        self.db_file = self.data_dir / "db.json"
        self.db_lock_file = self.data_dir / "db.json.lock"
        self.repos_file = self.data_dir / "repos.json"
        self.pins_file = self.data_dir / "pins.json"
        self.depmap_file = self.data_dir / "depmap.json"
        self.soname_file = self.data_dir / "soname-cache.json"
        self.audit_file = (Path("/var/log/debark/audit.log") if self.mode == "system"
                           else self.home_dir / ".local/state/debark/audit.log")
        self.snapshots_dir = self.data_dir / "snaps"
        self.profiles_dir = self.cfg_dir / "profiles"
        self.cfg_file = self.cfg_dir / "config.json"

    def _load(self) -> None:
        try:
            values = json.loads(read_regular_text(self.cfg_file))
        except (DebArkError, OSError, ValueError):
            return
        if not isinstance(values, dict):
            return

        def config_bool(key: str, default: bool) -> bool:
            value = values.get(key, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.casefold() in {"true", "false"}:
                return value.casefold() == "true"
            return default

        self.auto_yes = config_bool("auto_yes", self.auto_yes)
        self.colors = config_bool("colors", self.colors)
        self.experimental_features = config_bool("experimental_features", self.experimental_features)
        try:
            self.threads = max(1, min(16, int(values.get("threads", self.threads))))
        except (TypeError, ValueError):
            pass

    def ensure(self) -> None:
        self.validate()
        for directory in (self.cfg_dir, self.data_dir, self.cache_dir, self.pkgs_dir,
                          self.snapshots_dir, self.profiles_dir,
                          self.audit_file.parent, self.apps_root, self.bin_dir,
                          self.desktop_root, self.icons_root):
            try:
                validate_directory_chain(directory.parent)
                if directory.is_symlink():
                    raise DebArkError(f"Refusing to use a symlinked directory: {directory}")
                directory.mkdir(parents=True, exist_ok=True)
                validate_directory_chain(directory)
                if self.mode == "user":
                    set_user_owner(directory)
                    owner = user_owner()
                    if owner is not None:
                        parent = directory.parent
                        while parent == self.home_dir or self.home_dir in parent.parents:
                            set_user_owner(parent)
                            if parent == self.home_dir:
                                break
                            parent = parent.parent
            except PermissionError as exc:
                raise DebArkError(f"Cannot create {directory}. Use sudo or --user.") from exc
        self.validate()

    def validate(self) -> None:
        directories = (self.cfg_dir, self.data_dir, self.cache_dir, self.pkgs_dir,
                       self.snapshots_dir, self.profiles_dir, self.audit_file.parent,
                       self.apps_root, self.bin_dir, self.desktop_root, self.icons_root)
        for directory in directories:
            validate_directory_chain(directory)
        files = (self.cfg_file, self.db_file, self.db_lock_file, self.repos_file,
                 self.pins_file, self.depmap_file, self.soname_file, self.audit_file)
        for path in files:
            validate_directory_chain(path.parent)
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise DebArkError(f"Refusing to use a non-regular DebArk state file: {path}")

    def save(self, updates: dict[str, Any]) -> None:
        self.ensure()
        try:
            current = json.loads(read_regular_text(self.cfg_file))
        except (DebArkError, OSError, ValueError):
            current = {}
        current.setdefault("managed_by", "DebArk")
        current.setdefault("maintainer", AUTHOR)
        current.setdefault("repository", REPO)
        current.update(updates)
        current.update({
            "managed_by": "DebArk",
            "maintainer": AUTHOR,
            "repository": REPO,
        })
        write_json(self.cfg_file, current)
        if self.mode == "user":
            set_user_owner(self.cfg_file)
        self._load()

def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

class UI:
    enabled = False
    @classmethod
    def setup(cls, enabled: bool) -> None:
        cls.enabled = bool(enabled and sys.stdout.isatty() and not os.environ.get("NO_COLOR"))
    @classmethod
    def color(cls, value: str, code: str) -> str:
        return f"\033[{code}m{value}\033[0m" if cls.enabled else value


def progress(label: str, completed: int, total: int) -> None:
    """Draw a compact progress bar when output is attached to a terminal."""
    if not sys.stdout.isatty() or _OUTPUT_QUIET or _OUTPUT_JSON or total <= 0:
        return
    ratio = max(0.0, min(1.0, completed / total))
    columns = shutil.get_terminal_size((80, 24)).columns
    bar_width = max(10, min(28, columns - len(label) - 22))
    filled = int(ratio * bar_width)
    bar = UI.color("█" * filled, "32") + UI.color("░" * (bar_width - filled), "2")
    percent = int(ratio * 100)
    print(f"\r{label:<12} {bar} {percent:3d}%  {completed:,}/{total:,}",
          end="", flush=True)


def finish_progress() -> None:
    if sys.stdout.isatty() and not _OUTPUT_QUIET and not _OUTPUT_JSON:
        print()


def _root_managed_path(path: Path, recursive: bool = False) -> bool:
    """Return whether a system path and its contents are safe to execute as root."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    try:
        for index, part in enumerate(absolute.parts[1:]):
            current /= part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0:
                return False
            if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return False
            if index < len(absolute.parts[1:]) - 1 and not stat.S_ISDIR(metadata.st_mode):
                return False
        if recursive:
            if not stat.S_ISDIR(absolute.lstat().st_mode):
                return False
            for child in absolute.rglob("*"):
                metadata = child.lstat()
                if (stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0
                        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
                    return False
                if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                    return False
        return True
    except OSError:
        return False


def _trusted_system_program() -> Path | None:
    """Find the invoked DebArk entry point only when its code is root-controlled."""
    if Path(sys.argv[0]).name == "__main__.py":
        return None
    candidate = shutil.which(sys.argv[0]) or sys.argv[0]
    program = Path(os.path.abspath(candidate))
    package_dir = Path(__file__).resolve().parent
    if (_root_managed_path(program)
            and stat.S_ISREG(program.lstat().st_mode)
            and _root_managed_path(package_dir, recursive=True)):
        return program
    return None


def request_system_access() -> int:
    """Prompt through sudo/doas and restart the trusted installed command."""
    if not sys.stdin.isatty():
        raise DebArkError("System mode needs administrator access; run it from a terminal.")

    program = _trusted_system_program()
    if program is None:
        raise DebArkError(
            "Cannot request root access safely from this copy of DebArk. "
            "Install it system-wide with the official installer, then retry."
        )

    interpreter = Path(sys.executable).resolve()
    if (not _root_managed_path(interpreter)
            or not stat.S_ISREG(interpreter.lstat().st_mode)):
        raise DebArkError(
            "Cannot request root access safely with this Python interpreter. "
            "Use the system-installed DebArk command."
        )

    helper = next((Path(path) for path in ("/usr/bin/sudo", "/usr/bin/doas")
                   if _root_managed_path(Path(path))
                   and stat.S_ISREG(Path(path).lstat().st_mode)
                   and os.access(path, os.X_OK)), None)
    if helper is None:
        raise DebArkError("System mode requires sudo or doas; neither trusted helper was found.")

    print(f"{UI.color('[*]', '36')} Requesting system access…", file=sys.stderr)
    try:
        result = subprocess.run(
            [str(helper), str(interpreter), "-I", str(program), *sys.argv[1:]],
            check=False,
        )
    except OSError as exc:
        raise DebArkError(f"Could not start {helper.name}: {exc}") from exc
    return result.returncode

_OUTPUT_QUIET = False
_OUTPUT_JSON = False

def info(message: str) -> None:
    if not _OUTPUT_QUIET:
        print(f"{UI.color('[*]', '34')} {message}")

def ok(message: str) -> None:
    print(f"{UI.color('[✓]', '32')} {message}")

def warn(message: str) -> None:
    if not _OUTPUT_QUIET:
        print(f"{UI.color('[!]', '33')} {message}", file=sys.stderr)

def fail(message: str) -> None:
    raise DebArkError(message)

def header(title: str) -> None:
    width = max(52, len(title) + 8)
    border = "─" * (width - 2)
    label = title.center(width - 4)
    print(f"\n{UI.color(f'╭{border}╮', '36')}")
    print(f"{UI.color('│', '36')} {UI.color(label, '1;36')} {UI.color('│', '36')}")
    print(f"{UI.color(f'╰{border}╯', '36')}")

def section(title: str) -> None:
    label = f"── {title} "
    divider = "─" * max(4, 68 - len(label))
    print(f"\n{UI.color(label + divider, '34')}")

def confirm(prompt: str, auto_yes: bool = False) -> bool:
    if auto_yes:
        print(f"{UI.color('›', '36')} {prompt} [Y/n] {UI.color('(auto)', '2')}")
        return True
    try:
        text = f"{UI.color('›', '36')} {prompt} [Y/n] "
        if _OUTPUT_QUIET or _OUTPUT_JSON:
            print(text, end="", file=sys.stderr, flush=True)
            answer = input().strip().lower()
        else:
            answer = input(text).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in ("", "y", "yes")

def fmt_size(size: int) -> str:
    amount = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if amount < 1024:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"

def _setxattr(path: Path, name: str, value: str) -> None:
    try:
        os.setxattr(path, name, value.encode(), follow_symlinks=False)
    except (OSError, AttributeError, NotImplementedError):
        pass

def _getxattr(path: Path, name: str) -> str | None:
    try:
        return os.getxattr(path, name, follow_symlinks=False).decode()
    except (OSError, AttributeError, NotImplementedError):
        return None

def path_digest(path: Path) -> str:
    try:
        if path.is_symlink():
            data = ("symlink:" + os.readlink(path)).encode()
            return hashlib.sha256(data).hexdigest()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""

def mark_file(path: Path, package: str, digest: str, package_version: str = "") -> None:
    _setxattr(path, XATTR_PKG, package)
    if package_version:
        _setxattr(path, XATTR_VERSION, package_version)
    _setxattr(path, XATTR_INSTALLED, utc_now())
    if digest:
        _setxattr(path, XATTR_HASH, digest)

def _archive_rel(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in ("..", "") for part in path.parts):
        raise DebArkError(f"Unsafe path in package archive: {name!r}")
    parts = tuple(part for part in path.parts if part != ".")
    if not parts:
        raise DebArkError(f"Invalid path in package archive: {name!r}")
    return PurePosixPath(*parts)

def _normalize_extracted_symlinks(root: Path) -> None:
    root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        link = os.readlink(path)
        try:
            if link.startswith("/"):
                target = (root / link.lstrip("/")).resolve(strict=False)
                if not target.is_relative_to(root):
                    raise DebArkError(f"Package link escapes its payload: {path}")
                path.unlink()
                os.symlink(os.path.relpath(target, path.parent), path)
            else:
                target = (path.parent / link).resolve(strict=False)
                if not target.is_relative_to(root):
                    raise DebArkError(f"Package link escapes its payload: {path}")
        except (OSError, RuntimeError) as exc:
            raise DebArkError(f"Invalid package symlink {path}: {exc}") from exc

def _safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    members = archive.getmembers()
    by_name: dict[str, tarfile.TarInfo] = {}
    for member in members:
        if member.isdir() and member.name in (".", "./"):
            continue
        rel = _archive_rel(member.name)
        key = rel.as_posix()
        if key in by_name:
            raise DebArkError(f"Duplicate path in package archive: {key}")
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            raise DebArkError(f"Unsupported special file in package archive: {key}")
        by_name[key] = member
        if member.issym() or member.islnk():
            link = member.linkname
            if not link or (PurePosixPath(link).is_absolute() and not member.issym()):
                raise DebArkError(f"Unsafe link in package archive: {key}")
            if member.issym():
                resolved = posixpath.normpath(
                    link.lstrip("/") if link.startswith("/") else posixpath.join(posixpath.dirname(key), link)
                )
            else:
                resolved = posixpath.normpath(link)
            if resolved == ".." or resolved.startswith("../") or resolved.startswith("/"):
                raise DebArkError(f"Link escapes package tree: {key}")

    for key in by_name:
        parent = PurePosixPath(key).parent
        while parent.parts:
            parent_key = parent.as_posix()
            parent_member = by_name.get(parent_key)
            if parent_member and not parent_member.isdir():
                raise DebArkError(f"Archive entry is nested under a non-directory: {key}")
            parent = parent.parent

    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    for key, member in sorted(by_name.items(), key=lambda item: (item[0].count("/"), item[0])):
        if member.isdir():
            target = destination.joinpath(*PurePosixPath(key).parts)
            target.mkdir(parents=True, exist_ok=True)
    def safe_parent(target: Path) -> None:
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        current = parent
        while current != root and root in current.parents:
            if current.is_symlink():
                raise DebArkError(f"Archive path traverses a symlink: {target}")
            current = current.parent
        if parent.resolve() != root and root not in parent.resolve().parents:
            raise DebArkError(f"Archive path escapes extraction directory: {target}")

    deferred_links: list[tuple[str, tarfile.TarInfo]] = []
    for key, member in by_name.items():
        target = destination.joinpath(*PurePosixPath(key).parts)
        if member.isdir():
            continue
        safe_parent(target)
        if member.issym() or member.islnk():
            deferred_links.append((key, member))
            continue
        if target.exists() or target.is_symlink():
            raise DebArkError(f"Conflicting archive entry: {key}")
        stream = archive.extractfile(member)
        if stream is None:
            raise DebArkError(f"Cannot read package file: {key}")
        with stream, target.open("xb") as output:
            shutil.copyfileobj(stream, output)
        os.chmod(target, (member.mode & 0o777) | 0o400)

    for key, member in deferred_links:
        target = destination.joinpath(*PurePosixPath(key).parts)
        safe_parent(target)
        if target.exists() or target.is_symlink():
            raise DebArkError(f"Conflicting archive link: {key}")
        if member.issym():
            link = member.linkname
            if link.startswith("/"):
                resolved = posixpath.normpath(link.lstrip("/"))
                target_rel = os.path.relpath(destination / resolved, target.parent)
                os.symlink(target_rel, target)
            else:
                os.symlink(link, target)
        else:
            source = destination.joinpath(*PurePosixPath(posixpath.normpath(member.linkname)).parts)
            if not source.is_file() or source.is_symlink():
                raise DebArkError(f"Invalid hard link target in archive: {key}")
            os.link(source, target)

    for key, member in by_name.items():
        if member.isdir():
            target = destination.joinpath(*PurePosixPath(key).parts)
            os.chmod(target, (member.mode & 0o777) | 0o500)

def _run_checked(command: list[str], error: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise DebArkError(f"Required command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip()
        raise DebArkError(f"{error}{': ' + detail if detail else ''}") from exc

def _write_ar_member(deb: Path, member: str, target: Path) -> None:
    try:
        with target.open("wb") as stream:
            subprocess.run(["ar", "p", str(deb), member], check=True, stdout=stream, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise DebArkError("ar not found. Install binutils.") from exc
    except subprocess.CalledProcessError as exc:
        raise DebArkError(f"Cannot read {member} from package: {exc.stderr.decode(errors='replace')}") from exc

def _read_control_archive(archive_path: Path) -> dict[str, str]:
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            for member in archive.getmembers():
                if PurePosixPath(member.name).name == "control" and member.isfile():
                    stream = archive.extractfile(member)
                    if stream:
                        return parse_control(stream.read().decode(errors="replace"))
    except (tarfile.TarError, OSError):
        pass
    if shutil.which("bsdtar"):
        listing = _run_checked(["bsdtar", "-tf", str(archive_path)], "Cannot inspect control archive")
        candidates = [line for line in listing.stdout.decode(errors="replace").splitlines()
                      if PurePosixPath(line).name == "control"]
        if candidates:
            result = subprocess.run(["bsdtar", "-xOf", str(archive_path), candidates[0]],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode == 0:
                return parse_control(result.stdout.decode(errors="replace"))
    return {}

def extract_deb(deb: Path, workdir: Path) -> tuple[dict[str, str], Path]:
    listing = _run_checked(["ar", "t", str(deb)], "Not a readable Debian package")
    members = listing.stdout.decode(errors="replace").splitlines()
    data_name = next((name for name in members if name.startswith("data.tar")), None)
    control_name = next((name for name in members if name.startswith("control.tar")), None)
    if not data_name:
        raise DebArkError("Package has no data.tar archive.")
    data_archive = workdir / "data.archive"
    _write_ar_member(deb, data_name, data_archive)
    control: dict[str, str] = {}
    if control_name:
        control_archive = workdir / "control.archive"
        _write_ar_member(deb, control_name, control_archive)
        control = _read_control_archive(control_archive)
    data_dir = workdir / "payload"
    data_dir.mkdir()
    try:
        with tarfile.open(data_archive, mode="r:*") as archive:
            _safe_extract_tar(archive, data_dir)
    except (tarfile.TarError, OSError):
        if not shutil.which("bsdtar"):
            raise DebArkError("Unsupported package compression. Install libarchive (bsdtar).")
        listing = _run_checked(["bsdtar", "-tf", str(data_archive)], "Cannot inspect package archive")
        for name in listing.stdout.decode(errors="replace").splitlines():
            if name in (".", "./"):
                continue
            _archive_rel(name)
        _run_checked(["bsdtar", "--no-same-owner", "--no-same-permissions",
                      "-xf", str(data_archive), "-C", str(data_dir)], "Cannot extract package")
        _normalize_extracted_symlinks(data_dir)
    return control, data_dir

def parse_control(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current: str | None = None
    values: list[str] = []
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and current:
            values.append(line[1:])
        elif ":" in line:
            if current:
                fields[current] = "\n".join(values).strip()
            current, value = line.split(":", 1)
            current, values = current.strip(), [value.strip()]
        elif not line.strip() and current:
            fields[current] = "\n".join(values).strip()
            current, values = None, []
    if current:
        fields[current] = "\n".join(values).strip()
    return fields

def detect_license(control: dict[str, str], data_dir: Path, package: str) -> list[str]:
    values: list[str] = []
    for key in ("License", "X-License", "License-Expression"):
        value = control.get(key, "").strip()
        if value:
            values.extend(part.strip() for part in re.split(r"[,;]", value) if part.strip())
    docs = data_dir / "usr/share/doc"
    candidates = [docs / package / "copyright"]
    if docs.is_dir():
        candidates.extend(directory / "copyright" for directory in docs.iterdir()
                          if directory.is_dir() and directory.name.lower() == package.lower() and
                          directory != docs / package)
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            if path.stat().st_size > 1024 * 1024:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r"(?mi)^License:\s*([^\r\n]+)", content):
            value = match.group(1).strip()
            if value and value not in values:
                values.append(value)
    return values[:32]

def parse_dep_list(text: str) -> list[str]:
    result = []
    for group in text.split(","):
        match = re.match(r"\s*([A-Za-z0-9.+-]+)", group)
        if match:
            result.append(match.group(1).lower())
    return result

def _repo_packages() -> set[str]:
    global _PACMAN_REPOS_CACHE
    if _PACMAN_REPOS_CACHE is not None:
        return _PACMAN_REPOS_CACHE
    try:
        result = subprocess.run(["pacman", "-Slq"], check=True, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        _PACMAN_REPOS_CACHE = set(result.stdout.splitlines())
    except (OSError, subprocess.CalledProcessError):
        _PACMAN_REPOS_CACHE = set()
    return _PACMAN_REPOS_CACHE

_PACMAN_REPOS_CACHE: set[str] | None = None

def map_dep(name: str, cfg: Config) -> str | None:
    clean = name.split(":", 1)[0].lower()
    try:
        extra = json.loads(read_regular_text(cfg.depmap_file))
        if not isinstance(extra, dict):
            extra = {}
    except (OSError, ValueError):
        extra = {}
    mapping = {**BUILTIN_DEPMAP, **extra}
    if clean in mapping:
        mapped = mapping[clean]
        return mapped if isinstance(mapped, str) and PACKAGE_NAME.fullmatch(mapped) else None
    candidates = _repo_packages()
    if clean in candidates:
        return clean
    match = re.match(r"^(lib[a-z0-9+-]+?)[0-9][0-9.]*$", clean)
    if match and (match.group(1) in mapping or match.group(1) in candidates):
        mapped = mapping.get(match.group(1), match.group(1))
        return mapped if isinstance(mapped, str) and PACKAGE_NAME.fullmatch(mapped) else None
    if clean.endswith(("-dev", "-dbg", "-doc", "-dbgsym")):
        base = clean.rsplit("-", 1)[0]
        if base in mapping or base in candidates:
            mapped = mapping.get(base, base)
            return mapped if isinstance(mapped, str) and PACKAGE_NAME.fullmatch(mapped) else None
    return None

def analyze_elf_dependencies(data_dir: Path) -> dict[str, list[str]]:
    """Return ELF payload paths and their DT_NEEDED SONAMEs using readelf."""
    readelf = shutil.which("readelf")
    if not readelf:
        return {}
    analysis: dict[str, list[str]] = {}
    for path in data_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    continue
            result = subprocess.run([readelf, "-d", str(path)], text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode != 0:
            continue
        needed = re.findall(r"Shared library: \[([^]]+)\]", result.stdout)
        if needed:
            analysis[str(path.relative_to(data_dir))] = sorted(set(needed))
    return analysis

def map_soname(soname: str, cfg: Config, persist: bool = True) -> str | None:
    try:
        cache = json.loads(read_regular_text(cfg.soname_file))
        if isinstance(cache, dict) and cache.get(soname):
            value = cache[soname]
            return value if isinstance(value, str) and PACKAGE_NAME.fullmatch(value) else None
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    local_path = Path("/usr/lib") / soname
    owner = pacman_owns(local_path)
    if owner:
        cache[soname] = owner
        if persist:
            try:
                cfg.ensure()
                write_json(cfg.soname_file, cache)
                if cfg.mode == "user":
                    set_user_owner(cfg.soname_file)
            except DebArkError:
                pass
        return owner
    if not shutil.which("pacman"):
        return None
    try:
        result = subprocess.run(["pacman", "-Fq", str(local_path)], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    candidates = []
    for line in result.stdout.splitlines():
        token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        value = token.split("/", 1)[-1]
        if PACKAGE_NAME.fullmatch(value):
            candidates.append(value)
    mapped = sorted(set(candidates))[0] if candidates else None
    if mapped:
        cache[soname] = mapped
        if persist:
            try:
                cfg.ensure()
                write_json(cfg.soname_file, cache)
                if cfg.mode == "user":
                    set_user_owner(cfg.soname_file)
            except DebArkError:
                pass
    return mapped

def parse_apt_sources_file(path: Path) -> list[dict[str, Any]]:
    result = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("deb "):
            continue
        match = re.match(r"^deb\s+(?:\[[^]]*\]\s+)?(https?://\S+)\s+(\S+)\s+(.+)$", line)
        if match:
            result.append({"url": match.group(1), "suite": match.group(2),
                           "components": match.group(3).split()})
    return result

def pacman_owns(path: Path) -> str | None:
    try:
        result = subprocess.run(["pacman", "-Qo", str(path)], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            match = re.search(r"\bis owned by ([^\s]+)", result.stdout)
            if match:
                return match.group(1).split("/")[-1]
    except OSError:
        pass
    return None

def _normalize_manifest(name: str, value: dict[str, Any]) -> dict[str, Any]:
    """Normalize a legacy database entry to the current manifest shape."""
    legacy_hashes = value.get("sha256") if isinstance(value.get("sha256"), dict) else {}
    source_hash = value.get("source_sha256", "")
    if isinstance(value.get("sha256"), str):
        source_hash = value["sha256"]
    records: list[dict[str, Any]] = []
    raw_files = value.get("files", [])
    if isinstance(raw_files, list):
        for raw in raw_files:
            if isinstance(raw, dict):
                path = raw.get("path")
                if not isinstance(path, str):
                    continue
                record = dict(raw)
                record.setdefault("sha256", "")
            elif isinstance(raw, str):
                path = raw
                record = {
                    "path": path,
                    "sha256": legacy_hashes.get(path, ""),
                }
            else:
                continue
            try:
                stat = Path(path).lstat()
                record.setdefault("size", stat.st_size)
                record.setdefault("mode", f"{stat.st_mode & 0o7777:04o}")
            except OSError:
                record.setdefault("size", 0)
                record.setdefault("mode", "0000")
            record.setdefault("xattr_set", _getxattr(Path(path), XATTR_PKG) == name)
            records.append(record)
    normalized = dict(value)
    normalized.update({
        "schema_version": DB_SCHEMA_VERSION,
        "managed_by": "DebArk",
        "maintainer": AUTHOR,
        "repository": REPO,
        "name": value.get("name", name),
        "version": value.get("version", "0"),
        "source_deb": value.get("source_deb", ""),
        "sha256": source_hash,
        "mode": value.get("mode", "system"),
        "installed_at": value.get("installed_at", ""),
        "installed_by": value.get("installed_by", ""),
        "files": records,
        "debian_deps": value.get("debian_deps", []),
        "arch_deps": value.get("arch_deps", []),
        "isolated_libs": value.get("isolated_libs", []),
    })
    normalized.pop("source_sha256", None)
    return normalized

def manifest_files(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return file records while accepting old string-list manifests."""
    name = str(entry.get("name", ""))
    return _normalize_manifest(name, entry).get("files", [])

def load_db(cfg: Config) -> dict[str, Any]:
    try:
        value = json.loads(read_regular_text(cfg.db_file))
        if not isinstance(value, dict):
            return {}
        if value.get("schema_version") == DB_SCHEMA_VERSION and isinstance(value.get("packages"), dict):
            packages = value["packages"]
        else:
            packages = value
        return {
            name: _normalize_manifest(name, entry)
            for name, entry in packages.items()
            if isinstance(name, str) and PACKAGE_NAME.fullmatch(name) and isinstance(entry, dict)
        }
    except (OSError, ValueError):
        return {}

def save_db(cfg: Config, database: dict[str, Any], delete: set[str] | None = None) -> None:
    cfg.ensure()
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(cfg.db_lock_file, flags, 0o600)
        with os.fdopen(lock_fd, "r+") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            merged = load_db(cfg)
            for name in delete or set():
                merged.pop(name, None)
            for name, entry in database.items():
                merged[name] = entry
            normalized = {name: _normalize_manifest(name, entry)
                          for name, entry in merged.items()}
            write_json(cfg.db_file, {
                "schema_version": DB_SCHEMA_VERSION,
                "generated_by": {
                    "name": "DebArk",
                    "maintainer": AUTHOR,
                    "repository": REPO,
                },
                "packages": normalized,
            })
            if cfg.mode == "user":
                set_user_owner(cfg.db_file)
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise DebArkError(f"Cannot update package database {cfg.db_file}: {exc}") from exc

def append_audit(cfg: Config, event: dict[str, Any]) -> None:
    cfg.audit_file.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(cfg.audit_file, flags, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as stream:
            record = {
                **event,
                "tool": "DebArk",
                "maintainer": AUTHOR,
                "repository": REPO,
            }
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(cfg.audit_file, 0o600)
        if cfg.mode == "user":
            set_user_owner(cfg.audit_file)
    except OSError as exc:
        raise DebArkError(f"Cannot write audit log {cfg.audit_file}: {exc}") from exc

def create_snapshot(cfg: Config, package: str, entry: dict[str, Any]) -> str:
    """Archive verified managed payload files for a later local rollback."""
    app_dir = cfg.apps_root / package
    if app_dir.is_symlink() or Path(entry.get("app_dir", "")).absolute() != app_dir.absolute():
        fail("Database contains an invalid application path; refusing to snapshot it.")
    records = manifest_files(entry)
    for record in records:
        path = Path(record["path"])
        if not _under(path, app_dir):
            continue
        if not _under(path.parent.resolve(), app_dir.resolve()):
            fail(f"Manifest path is outside the package directory: {path}")
        if not (path.exists() or path.is_symlink()) or path_digest(path) != record.get("sha256", ""):
            fail(f"Cannot snapshot modified or missing file: {path}")
    cfg.ensure()
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshot_dir = cfg.snapshots_dir / package / snapshot_id
    snapshot_dir.mkdir(parents=True, mode=0o700)
    archive = snapshot_dir / "files.tar.gz"
    temp_archive = snapshot_dir / ".files.tar.gz.tmp"
    try:
        with tarfile.open(temp_archive, "w:gz", compresslevel=6) as bundle:
            for record in records:
                path = Path(record["path"])
                if not _under(path, app_dir):
                    continue
                relative = path.relative_to(app_dir)
                bundle.add(path, arcname=relative.as_posix(), recursive=False)
        os.replace(temp_archive, archive)
        snapshot_entry = dict(entry)
        snapshot_entry.pop("snapshots", None)
        write_json(snapshot_dir / "manifest.json", snapshot_entry)
        if cfg.mode == "user":
            set_user_owner(snapshot_dir, recursive=True)
            set_user_owner(snapshot_dir.parent)
        snapshots = list(entry.get("snapshots", []))
        snapshots.append({"id": snapshot_id, "archive": str(archive),
                          "created_at": utc_now(), "version": entry.get("version", "0")})
        entry["snapshots"] = snapshots
        entry["snapshot"] = {"id": snapshot_id, "type": "local-tar", "path": str(snapshot_dir)}
        return snapshot_id
    except Exception:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        raise

def restore_snapshot(cfg: Config, package: str, entry: dict[str, Any], snapshot_id: str) -> None:
    app_dir = cfg.apps_root / package
    if app_dir.is_symlink() or Path(entry.get("app_dir", "")).absolute() != app_dir.absolute():
        fail("Database contains an invalid application path; refusing rollback.")
    actual: set[str] = set()
    if app_dir.exists():
        _, paths = _iter_payload(app_dir)
        actual = {str(path) for path in paths}
    records = manifest_files(entry)
    expected = {record["path"] for record in records if _under(Path(record["path"]), app_dir)}
    if actual != expected:
        fail("Current package tree has untracked files; clean it before rollback.")
    snapshot_dir = cfg.snapshots_dir / package / snapshot_id
    if not _under(snapshot_dir, cfg.snapshots_dir) or snapshot_dir.is_symlink():
        fail("Invalid snapshot location.")
    archive = snapshot_dir / "files.tar.gz"
    metadata_path = snapshot_dir / "manifest.json"
    if not archive.is_file() or archive.is_symlink() or not metadata_path.is_file():
        fail(f"Snapshot is incomplete: {snapshot_id}")
    try:
        saved_entry = json.loads(read_regular_text(metadata_path))
    except (OSError, ValueError) as exc:
        raise DebArkError(f"Could not read snapshot metadata: {exc}") from exc
    if not isinstance(saved_entry, dict) or saved_entry.get("name") != package:
        fail("Snapshot metadata does not match the requested package.")
    cfg.apps_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".debark-rollback-{package}-", dir=cfg.apps_root) as temp_name:
        temp_root = Path(temp_name)
        stage = temp_root / package
        stage.mkdir(mode=0o700)
        try:
            with tarfile.open(archive, "r:gz") as bundle:
                _safe_extract_tar(bundle, stage)
        except (OSError, tarfile.TarError) as exc:
            raise DebArkError(f"Could not extract snapshot: {exc}") from exc
        saved_records = manifest_files(saved_entry)
        for record in saved_records:
            source_path = Path(record["path"])
            if not _under(source_path, app_dir):
                continue
            relative = source_path.relative_to(app_dir)
            restored = stage / relative
            if not (restored.exists() or restored.is_symlink()) or path_digest(restored) != record.get("sha256", ""):
                fail(f"Snapshot verification failed for {relative}.")
        if cfg.mode == "user":
            set_user_owner(stage, recursive=True)
        os.chmod(stage, 0o755)
        backup = cfg.apps_root / f".{package}.rollback-{os.getpid()}"
        if backup.exists() or backup.is_symlink():
            fail(f"Rollback staging path already exists: {backup}")
        os.replace(app_dir, backup)
        try:
            os.replace(stage, app_dir)
        except Exception:
            os.replace(backup, app_dir)
            raise
        shutil.rmtree(backup, ignore_errors=True)
    saved_entry["app_dir"] = str(app_dir)
    saved_entry["mode"] = cfg.mode
    saved_entry["snapshots"] = entry.get("snapshots", [])
    saved_entry["snapshot"] = entry.get("snapshot")
    for record in manifest_files(saved_entry):
        restored = Path(record["path"])
        mark_file(restored, package, record.get("sha256", ""),
                  str(saved_entry.get("version", "")))
        record["xattr_set"] = _getxattr(restored, XATTR_PKG) == package
    entry.clear()
    entry.update(saved_entry)

def load_repos(cfg: Config) -> dict[str, Any]:
    try:
        value = json.loads(read_regular_text(cfg.repos_file))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def load_pins(cfg: Config) -> dict[str, str]:
    try:
        value = json.loads(read_regular_text(cfg.pins_file))
        if not isinstance(value, dict):
            return {}
        return {name: version for name, version in value.items()
                if isinstance(name, str) and PACKAGE_NAME.fullmatch(name) and
                isinstance(version, str) and version}
    except (OSError, ValueError):
        return {}

def save_repos(cfg: Config, repos: dict[str, Any]) -> None:
    cfg.ensure()
    write_json(cfg.repos_file, repos)
    if cfg.mode == "user":
        set_user_owner(cfg.repos_file)

def download(url: str, dest: Path, user_mode: bool = False,
             cfg: Config | None = None) -> Path:
    if not url.startswith(("https://", "http://")):
        raise DebArkError("Only HTTP and HTTPS package URLs are supported.")
    if cfg is not None:
        cfg.ensure()
    dest.parent.mkdir(parents=True, exist_ok=True)
    info(f"Downloading {url}")
    fd, temp_name = tempfile.mkstemp(prefix=f".{dest.name}.download-", dir=dest.parent)
    temp_path = Path(temp_name)
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=45) as response, os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            total = int(response.headers.get("content-length", "0") or 0)
            amount = 0
            while True:
                block = response.read(1024 * 128)
                if not block:
                    break
                output.write(block)
                amount += len(block)
                if total:
                    progress("Downloading", amount, total)
            output.flush()
            os.fsync(output.fileno())
        if total:
            finish_progress()
        os.replace(temp_path, dest)
        if user_mode:
            set_user_owner(dest)
    except (urllib.error.URLError, OSError) as exc:
        if total:
            finish_progress()
        with contextlib.suppress(OSError):
            os.close(fd)
        temp_path.unlink(missing_ok=True)
        raise DebArkError(f"Download failed: {exc}") from exc
    except Exception:
        if total:
            finish_progress()
        with contextlib.suppress(OSError):
            os.close(fd)
        temp_path.unlink(missing_ok=True)
        raise
    return dest

def retain_source_package(source: Path, cfg: Config, package: str, version: str,
                         expected_digest: str = "") -> Path:
    """Keep a private verified source copy for repair and reproducible rollback."""
    cfg.ensure()
    digest = expected_digest or path_digest(source)
    if not digest:
        fail("Could not compute the source package SHA256.")
    try:
        if source.resolve().parent == cfg.cache_dir.resolve() and not source.is_symlink():
            os.chmod(source, 0o600)
            if cfg.mode == "user":
                set_user_owner(source)
            return source
    except OSError:
        pass
    safe_version = re.sub(r"[^A-Za-z0-9.+:~_-]", "_", version)[:96] or "0"
    destination = cfg.cache_dir / f"{package}-{safe_version}-{digest[:12]}.deb"
    if source.absolute() == destination.absolute():
        os.chmod(destination, 0o600)
        if cfg.mode == "user":
            set_user_owner(destination)
        return destination
    if destination.exists() or destination.is_symlink():
        if destination.is_file() and not destination.is_symlink() and path_digest(destination) == digest:
            os.chmod(destination, 0o600)
            if cfg.mode == "user":
                set_user_owner(destination)
            return destination
        fail(f"A conflicting cache path exists for the source package: {destination}")
    fd, temp_name = tempfile.mkstemp(prefix=".source-", suffix=".deb", dir=cfg.cache_dir)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
            os.fchmod(output.fileno(), 0o600)
            shutil.copyfileobj(input_stream, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if path_digest(temp_path) != digest:
            fail("The source package changed while it was copied to the DebArk cache.")
        os.replace(temp_path, destination)
        if cfg.mode == "user":
            set_user_owner(destination)
        return destination
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

def _iter_payload(src: Path) -> tuple[list[Path], list[Path]]:
    directories: list[Path] = []
    files: list[Path] = []
    for root, dirnames, filenames in os.walk(src, followlinks=False):
        current = Path(root)
        keep = []
        for name in dirnames:
            path = current / name
            if path.is_symlink():
                files.append(path)
            else:
                directories.append(path)
                keep.append(name)
        dirnames[:] = keep
        files.extend(current / name for name in filenames)
    return directories, files

def copy_payload(src: Path, destination: Path, cfg: Config, package: str,
                 package_version: str = "") -> tuple[list[str], dict[str, str]]:
    directories, files = _iter_payload(src)
    destination.mkdir(parents=True, exist_ok=True)
    for directory in sorted(directories, key=lambda p: len(p.parts)):
        target = destination / directory.relative_to(src)
        target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    hashes: dict[str, str] = {}
    errors: list[str] = []
    lock = threading.Lock()
    def copy_one(source: Path) -> None:
        relative = source.relative_to(src)
        target = destination / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                os.symlink(os.readlink(source), target)
            else:
                shutil.copy2(source, target, follow_symlinks=False)
            digest = path_digest(target)
            mark_file(target, package, digest, package_version)
            with lock:
                written.append(str(target))
                hashes[str(target)] = digest
        except OSError as exc:
            with lock:
                errors.append(f"{source}: {exc}")
    total = len(files)
    completed = 0
    info(f"Copying {total} payload file(s) with {cfg.threads} worker(s)")
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.threads) as executor:
        futures = [executor.submit(copy_one, path) for path in files]
        for future in concurrent.futures.as_completed(futures):
            future.result()
            completed += 1
            progress("Copying", completed, total)
    if total:
        finish_progress()
    if errors:
        raise DebArkError("Could not copy package payload: " + "; ".join(errors[:5]))
    for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
        target = destination / directory.relative_to(src)
        try:
            shutil.copystat(directory, target, follow_symlinks=False)
            os.chmod(target, directory.stat(follow_symlinks=False).st_mode & 0o777)
        except OSError as exc:
            raise DebArkError(f"Could not preserve directory metadata for {directory}: {exc}") from exc
    return written, hashes

def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(4)
        return header == b"\x7fELF" or header.startswith(b"#!")
    except OSError:
        return False

def find_executables(data_dir: Path, package: str) -> list[Path]:
    roots = [data_dir / "usr/bin", data_dir / "usr/local/bin", data_dir / "bin",
             data_dir / "opt"]
    result: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        iterator = root.rglob("*") if root.name == "opt" else root.iterdir()
        for path in iterator:
            if path.is_file() and os.access(path, os.X_OK):
                try:
                    resolved = path.resolve()
                    if resolved.is_relative_to(data_dir.resolve()) and _is_binary(resolved):
                        result.append(path)
                except (OSError, RuntimeError):
                    pass
    preferred = [p for p in result if p.name.lower() in (package.lower(), package.lower() + ".bin")]
    return list(dict.fromkeys(preferred + sorted(result, key=lambda p: (str(p).count("/"), str(p)))))

def find_main_executable(data_dir: Path, package: str) -> Path | None:
    candidates = find_executables(data_dir, package)
    return candidates[0] if candidates else None

def find_icons(data_dir: Path) -> list[Path]:
    icons = []
    for root in (data_dir / "usr/share/icons", data_dir / "usr/share/pixmaps",
                 data_dir / "opt"):
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in (".png", ".svg", ".xpm"):
                    icons.append(path)
    return icons

def _private_library_dirs(app_root: Path) -> list[Path]:
    return sorted({path.parent for path in app_root.rglob("*")
                   if (path.is_file() or path.is_symlink()) and
                   (".so" in path.name or path.suffix.lower() in (".dylib", ".dll"))})

def make_wrapper(path: Path, app_root: Path, executable: Path, cfg: Config,
                 payload_root: Path, sandbox: bool = False) -> None:
    relative = executable.relative_to(app_root)
    command = ["#!/bin/sh"]
    libraries = [app_root / item.relative_to(payload_root)
                 for item in _private_library_dirs(payload_root)]
    if libraries:
        value = ":".join(str(item) for item in libraries)
        command += [
            f"DEBARK_LIBS={shlex.quote(value)}",
        ]
        if not sandbox:
            command += [
                'if [ -n "$DEBARK_LIBS" ]; then',
                '  if [ -n "$LD_LIBRARY_PATH" ]; then DEBARK_LIBS="$DEBARK_LIBS:$LD_LIBRARY_PATH"; fi',
                '  export LD_LIBRARY_PATH="$DEBARK_LIBS"',
                "fi",
            ]
    executable_path = shlex.quote(str(app_root / relative))
    if sandbox:
        bwrap_command = [
            "  exec bwrap \\",
            "    --die-with-parent \\",
            "    --new-session \\",
            "    --unshare-all \\",
            "    --ro-bind / / \\",
            "    --dev /dev \\",
            "    --proc /proc \\",
            "    --tmpfs /tmp \\",
            "    --tmpfs /home \\",
            "    --tmpfs /root \\",
            "    --tmpfs /run/user \\",
            "    --dir /tmp/debark-home \\",
            "    --clearenv \\",
            "    --setenv PATH /usr/local/sbin:/usr/local/bin:/usr/bin:/bin \\",
            "    --setenv LANG \"${LANG:-C.UTF-8}\" \\",
            "    --setenv HOME /tmp/debark-home \\",
            "    --setenv XDG_CONFIG_HOME /tmp/debark-home/.config \\",
            "    --setenv XDG_CACHE_HOME /tmp/debark-home/.cache \\",
            "    --setenv XDG_DATA_HOME /tmp/debark-home/.local/share \\",
            "    --setenv XDG_STATE_HOME /tmp/debark-home/.local/state \\",
        ]
        if libraries:
            bwrap_command.append("    --setenv LD_LIBRARY_PATH \"$DEBARK_LIBS\" \\")
        bwrap_command.append(f"    -- {executable_path} \"$@\"")
        firejail_command = [
            "  exec env -i \\",
            "    PATH=/usr/local/sbin:/usr/local/bin:/usr/bin:/bin \\",
            "    LANG=\"${LANG:-C.UTF-8}\" \\",
        ]
        if libraries:
            firejail_command.append("    LD_LIBRARY_PATH=\"$DEBARK_LIBS\" \\")
        firejail_command += [
            "    firejail \\",
            "      --net=none \\",
            "      --private \\",
            "      --private-tmp \\",
            "      --private-dev \\",
            "      --blacklist=/run/user \\",
            "      --read-only=/ \\",
            "      --seccomp \\",
            "      --caps.drop=all \\",
            "      --nonewprivs \\",
            f"      -- {executable_path} \"$@\"",
        ]
        command += [
            "unset DBUS_SESSION_BUS_ADDRESS SSH_AUTH_SOCK GPG_AGENT_INFO XAUTHORITY DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR XDG_CONFIG_HOME XDG_CACHE_HOME XDG_DATA_HOME XDG_STATE_HOME LD_LIBRARY_PATH",
            "if command -v bwrap >/dev/null 2>&1; then",
            *bwrap_command,
            "elif command -v firejail >/dev/null 2>&1; then",
            *firejail_command,
            "else",
            "  printf '%s\\n' 'DebArk: bubblewrap or firejail is required for this launcher.' >&2",
            "  exit 127",
            "fi",
        ]
    else:
        command += [f"exec {executable_path} \"$@\""]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write("\n".join(command) + "\n")
    os.chmod(path, 0o755)

def _safe_output_path(path: Path, cfg: Config, prior: dict[str, Any]) -> None:
    if not path.exists() and not path.is_symlink():
        return
    owner = pacman_owns(path)
    detail = f" (owned by {owner})" if owner else ""
    raise DebArkError(f"Refusing to replace existing file: {path}{detail}")

def _desktop_content(source: Path, launcher: Path, icon: Path | None) -> str:
    try:
        content = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        content = "[Desktop Entry]\nType=Application\nName=Application\n"
    lines = content.splitlines()
    result: list[str] = []
    in_entry = False
    has_exec = has_type = has_terminal = has_categories = False
    for line in lines:
        if line.strip().startswith("["):
            if in_entry:
                if not has_exec:
                    result.append(f"Exec={json.dumps(str(launcher), ensure_ascii=False)} %U")
                if not has_type:
                    result.append("Type=Application")
                if not has_terminal:
                    result.append("Terminal=false")
                if not has_categories:
                    result.append("Categories=Utility;")
            in_entry = line.strip() == "[Desktop Entry]"
            has_exec = has_type = has_terminal = has_categories = False
            result.append(line)
        elif in_entry and line.startswith("Exec="):
            result.append(f"Exec={json.dumps(str(launcher), ensure_ascii=False)} %U")
            has_exec = True
        elif in_entry and line.startswith("Icon=") and icon:
            result.append(f"Icon={icon}")
        else:
            result.append(line)
            if in_entry:
                has_exec |= line.startswith("Exec=")
                has_type |= line.startswith("Type=")
                has_terminal |= line.startswith("Terminal=")
                has_categories |= line.startswith("Categories=")
    if not any(line.strip() == "[Desktop Entry]" for line in result):
        result = ["[Desktop Entry]", "Type=Application", "Name=Application",
                  f"Exec={json.dumps(str(launcher), ensure_ascii=False)} %U",
                  "Terminal=false", "Categories=Utility;"]
    elif in_entry:
        if not has_exec:
            result.append(f"Exec={json.dumps(str(launcher), ensure_ascii=False)} %U")
        if not has_type:
            result.append("Type=Application")
        if not has_terminal:
            result.append("Terminal=false")
        if not has_categories:
            result.append("Categories=Utility;")
    return "\n".join(result).rstrip() + "\n"

def _record_file(path: Path, package: str, files: list[str], hashes: dict[str, str],
                 package_version: str = "") -> None:
    digest = path_digest(path)
    mark_file(path, package, digest, package_version)
    files.append(str(path))
    hashes[str(path)] = digest

def _install_desktops(data_dir: Path, app_root: Path, package: str, launcher: Path,
                      cfg: Config, files: list[str], hashes: dict[str, str],
                      prior: dict[str, Any], package_version: str = "") -> None:
    sources = sorted(data_dir.rglob("*.desktop"))
    if not sources:
        return
    icons = find_icons(data_dir)
    icon_source = next((p for p in icons if p.stem.lower() in (package.lower(), launcher.name.lower())), None)
    icon_source = icon_source or (icons[0] if icons else None)
    icon = app_root / icon_source.relative_to(data_dir) if icon_source else None
    created: list[Path] = []
    try:
        for index, source in enumerate(sources, start=1):
            target = cfg.desktop_root / f"{package}-{index}-{source.name}"
            _safe_output_path(target, cfg, prior)
            target.parent.mkdir(parents=True, exist_ok=True)
            content = _desktop_content(source, launcher, icon)
            with target.open("x", encoding="utf-8") as output:
                output.write(content)
            created.append(target)
            _record_file(target, package, files, hashes, package_version)
    except Exception:
        for target in created:
            target.unlink(missing_ok=True)
        raise

def _write_launchers(executables: list[Path], main: Path | None, data_dir: Path, app_stage: Path,
                     app_root: Path, package: str, cfg: Config,
                     files: list[str], hashes: dict[str, str], prior: dict[str, Any],
                     sandbox: bool = False, package_version: str = "") -> Path | None:
    if not executables:
        return None
    counts: dict[str, int] = {}
    for executable in executables:
        name = executable.name
        if COMMAND_NAME.fullmatch(name):
            counts[name] = counts.get(name, 0) + 1
    launchers: dict[Path, Path] = {}
    duplicate_numbers: dict[str, int] = {}
    for executable in executables:
        base_name = executable.name
        if not COMMAND_NAME.fullmatch(base_name):
            continue
        name = base_name
        if counts.get(base_name, 0) > 1:
            duplicate_numbers[base_name] = duplicate_numbers.get(base_name, 0) + 1
            name = f"{package}-{base_name}-{duplicate_numbers[base_name]}"
        target = cfg.bin_dir / name
        if target.exists() or target.is_symlink():
            name = f"{package}-{base_name}"
            target = cfg.bin_dir / name
            suffix = 2
            while target.exists() or target.is_symlink():
                target = cfg.bin_dir / f"{name}-{suffix}"
                suffix += 1
        _safe_output_path(target, cfg, prior)
        launchers[executable] = target
    cfg.bin_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        for executable, target in launchers.items():
            _safe_output_path(target, cfg, prior)
            make_wrapper(target, app_root, app_root / executable.relative_to(data_dir), cfg,
                         app_stage, sandbox=sandbox)
            created.append(target)
            _record_file(target, package, files, hashes, package_version)
    except Exception:
        for target in created:
            target.unlink(missing_ok=True)
        raise
    selected = None
    if main:
        selected = next((path for exe, path in launchers.items()
                         if exe.name == main.name and exe.parent == main.parent), None)
    return selected or next(iter(launchers.values()), None)

def _arch_compatible(debian_arch: str) -> bool:
    current = platform.machine().lower()
    expected = {
        "x86_64": {"amd64", "all"},
        "aarch64": {"arm64", "all"},
        "armv7l": {"armhf", "all"},
        "i686": {"i386", "all"},
    }.get(current, {"all"})
    return debian_arch in expected

def cmd_install(args: argparse.Namespace, cfg: Config) -> None:
    if cfg.mode == "system" and os.geteuid() != 0:
        fail("System install requires root. Use --user for an unprivileged install.")
    if cfg.mode == "user" and os.geteuid() == 0 and os.environ.get("SUDO_USER"):
        fail("Run user-mode installs without sudo; use sudo only for system mode.")
    cfg.validate()
    source_arg = args.deb
    if source_arg.startswith(("http://", "https://")):
        filename = Path(source_arg.split("?", 1)[0].rstrip("/")).name or "package.deb"
        if not filename.endswith(".deb"):
            filename += ".deb"
        source = download(source_arg, cfg.cache_dir / filename,
                          user_mode=cfg.mode == "user", cfg=cfg)
    else:
        source = Path(source_arg).expanduser().resolve()
    if not source.is_file():
        fail(f"Package file not found: {source}")
    expected_source_hash = getattr(args, "expected_sha256", "")
    expected_source_hash = expected_source_hash or getattr(args, "sha256", "")
    if expected_source_hash and not re.fullmatch(r"[A-Fa-f0-9]{64}", str(expected_source_hash)):
        fail("Expected SHA256 must contain exactly 64 hexadecimal characters.")
    source_fingerprint = path_digest(source)
    if not source_fingerprint:
        fail("Could not compute the source package SHA256.")
    if expected_source_hash and source_fingerprint.lower() != str(expected_source_hash).lower():
        fail("Package SHA256 does not match the expected value.")
    signature_value = getattr(args, "gpg_signature", None)
    keyring_value = getattr(args, "keyring", None)
    if bool(signature_value) != bool(keyring_value):
        fail("GPG verification requires both --gpg-signature and --keyring.")
    if signature_value and keyring_value:
        signature = Path(signature_value).expanduser().resolve()
        keyring = Path(keyring_value).expanduser().resolve()
        if not signature.is_file() or not keyring.is_file():
            fail("The specified GPG signature or keyring does not exist.")
        _run_checked(["gpgv", "--keyring", str(keyring), str(signature), str(source)],
                     "GPG signature verification failed")
    sandbox_requested = bool(getattr(args, "sandbox", False))
    sandbox_available = bool(shutil.which("firejail") or shutil.which("bwrap"))
    if sandbox_requested and not sandbox_available:
        fail("Sandbox launchers require firejail or bubblewrap to be installed.")

    prior_db = load_db(cfg)
    temp_owner = tempfile.TemporaryDirectory(prefix="debark-")
    app_stage: Path | None = None
    created_outputs: list[Path] = []
    final_app: Path | None = None
    app_created = False
    db_saved = False
    package: str | None = None
    try:
        temp = Path(temp_owner.name)
        control, data_dir = extract_deb(source, temp)
        if path_digest(source) != source_fingerprint:
            fail("The source package changed while it was being analyzed.")
        package = control.get("Package", source.stem).strip().lower()
        version = control.get("Version", "0").strip()
        requested_version = getattr(args, "requested_version", None)
        if requested_version and version != requested_version:
            fail(f"Package version {version} does not match the requested version {requested_version}.")
        architecture = control.get("Architecture", "all").strip().lower()
        if not PACKAGE_NAME.fullmatch(package):
            fail(f"Invalid Debian package name: {package!r}")
        pinned = load_pins(cfg).get(package)
        if pinned and pinned != version:
            fail(f"Package {package} is pinned to version {pinned}; source contains {version}.")
        if package in prior_db:
            fail(f"{package} is already registered. Remove it before installing another version.")
        if not _arch_compatible(architecture):
            fail(f"Package architecture {architecture} does not match this machine ({platform.machine()}).")
        license_values = detect_license(control, data_dir, package)
        final_app = cfg.apps_root / package
        if final_app.exists() or final_app.is_symlink():
            fail(f"Refusing to replace existing application directory: {final_app}")

        debian_deps = parse_dep_list(control.get("Depends", ""))
        arch_deps: list[str] = []
        unmapped: list[str] = []
        for dependency in debian_deps:
            mapped = map_dep(dependency, cfg)
            if mapped:
                arch_deps.append(mapped)
            else:
                unmapped.append(dependency)
        elf_analysis = analyze_elf_dependencies(data_dir)
        elf_sonames = sorted({soname for values in elf_analysis.values() for soname in values})
        bundled_library_names = {
            path.name for path in data_dir.rglob("*")
            if (path.is_file() or path.is_symlink()) and ".so" in path.name
        }
        unmapped_sonames: list[str] = []
        soname_mappings: dict[str, str] = {}
        for soname in elf_sonames:
            if (Path("/usr/lib") / soname).exists() or soname in bundled_library_names:
                continue
            mapped = map_soname(soname, cfg, persist=False)
            if mapped:
                arch_deps.append(mapped)
                soname_mappings[soname] = mapped
            else:
                unmapped_sonames.append(soname)
        arch_deps = sorted(set(arch_deps))
        protected_missing: list[str] = []
        installed = set()
        for arch_name in arch_deps:
            try:
                query = subprocess.run(["pacman", "-Qq", arch_name], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                if query.returncode == 0:
                    installed.add(arch_name)
            except OSError:
                break
        protected_missing = sorted((set(arch_deps) - installed) & PROTECTED_ARCH_PACKAGES)
        if protected_missing:
            fail("Required protected Arch base package(s) are missing: " +
                 ", ".join(protected_missing))
        missing = [name for name in arch_deps if name not in installed and
                   name not in PROTECTED_ARCH_PACKAGES]
        executables = find_executables(data_dir, package)
        main_exec = find_main_executable(data_dir, package)

        header(f"DebArk · install {package}")
        print(f"Package: {package}\nVersion: {version}\nArchitecture: {architecture}")
        print(f"Mode: {cfg.mode}\nPackage size: {fmt_size(source.stat().st_size)}")
        print(f"Application files: {final_app}")
        print(f"Launcher directory: {cfg.bin_dir}")
        section(f"Dependencies ({len(debian_deps)} Debian → {len(arch_deps)} Arch)")
        for dependency in debian_deps:
            mapped = map_dep(dependency, cfg)
            if mapped:
                print(f"  {dependency} → {mapped}" + (" (installed)" if mapped in installed else " (missing)"))
            else:
                ranked = rank_candidates(dependency, _repo_packages(), limit=1)
                if ranked:
                    candidate, score = ranked[0]
                    print(f"  {dependency} → unmapped (offline hint: {candidate}, rank {score:.2f})")
                else:
                    print(f"  {dependency} → unmapped")
        if unmapped:
            warn(f"{len(unmapped)} dependency name(s) could not be mapped.")
        if unmapped_sonames:
            warn(f"{len(unmapped_sonames)} ELF shared library name(s) have no Arch provider mapping.")
        if not shutil.which("readelf"):
            warn("readelf is unavailable; ELF dependency analysis was skipped.")
        if missing and cfg.mode == "user":
            warn("Missing Arch dependencies cannot be installed in user mode; install them with pacman separately.")
        if not executables:
            warn("No executable was found; this package will be stored without launchers.")
        if args.dry_run:
            print(f"\nDry run: payload would be extracted into {final_app}.")
            if missing and not args.no_deps and cfg.mode == "system":
                print("Arch dependencies to install: " + ", ".join(missing))
            return
        if not confirm(f"Proceed with {package}?", args.yes or cfg.auto_yes):
            warn("Aborted.")
            return
        plugin_context = {"package": package, "version": version, "mode": cfg.mode,
                          "source": str(source), "app_dir": str(final_app),
                          "debian_dependencies": debian_deps, "arch_dependencies": arch_deps}
        plugin_errors = run_hook(cfg, "on_pre_install", plugin_context)
        if plugin_errors:
            for message in plugin_errors:
                warn(message)
            fail("Install stopped because an enabled pre-install hook failed.")
        cfg.ensure()
        if soname_mappings:
            try:
                cached = json.loads(read_regular_text(cfg.soname_file))
            except (DebArkError, OSError, ValueError):
                cached = {}
            if not isinstance(cached, dict):
                cached = {}
            cached.update(soname_mappings)
            write_json(cfg.soname_file, cached)
            if cfg.mode == "user":
                set_user_owner(cfg.soname_file)
        if missing and not args.no_deps and cfg.mode == "system":
            command = ["pacman", "-S", "--needed"]
            if args.yes or cfg.auto_yes:
                command.append("--noconfirm")
            command.extend(missing)
            _run_checked(command, "Could not install Arch dependencies")

        source = retain_source_package(source, cfg, package, version,
                                      expected_digest=source_fingerprint)

        cfg.apps_root.mkdir(parents=True, exist_ok=True)
        app_stage = cfg.apps_root / f".{package}.staging-{os.getpid()}"
        if app_stage.exists():
            fail(f"Staging path already exists; refusing to replace it: {app_stage}")
        app_stage.mkdir(mode=0o755)
        written, hashes = copy_payload(data_dir, app_stage, cfg, package, version)
        stage_path = app_stage
        launcher = _write_launchers(executables, main_exec, data_dir, app_stage, final_app,
                                    package, cfg, written, hashes, prior_db,
                                    sandbox=sandbox_requested,
                                    package_version=version)
        created_outputs = [Path(path) for path in written if _under(Path(path), cfg.bin_dir)]
        if launcher:
            _install_desktops(data_dir, final_app, package, launcher, cfg,
                              written, hashes, prior_db, package_version=version)
            created_outputs = [Path(path) for path in written
                               if _under(Path(path), cfg.bin_dir) or _under(Path(path), cfg.desktop_root)]
        os.replace(app_stage, final_app)
        if cfg.mode == "user":
            set_user_owner(final_app, recursive=True)
        app_stage = None
        app_created = True
        relocated_files: list[str] = []
        relocated_hashes: dict[str, str] = {}
        for raw_path in written:
            old_path = Path(raw_path)
            try:
                relative = old_path.relative_to(stage_path)
                new_path = final_app / relative
            except ValueError:
                new_path = old_path
            relocated_files.append(str(new_path))
            relocated_hashes[str(new_path)] = hashes.get(raw_path, "")
            if cfg.mode == "user":
                set_user_owner(new_path)
        written, hashes = relocated_files, relocated_hashes
        if getattr(args, "verify_after", True):
            mismatched = [path for path in written if path_digest(Path(path)) != hashes.get(path, "")]
            if mismatched:
                fail(f"Post-install integrity check failed for {len(mismatched)} managed file(s).")
        installed_at = utc_now()
        file_records: list[dict[str, Any]] = []
        desktop_files: list[str] = []
        for raw_path in written:
            path = Path(raw_path)
            try:
                metadata = path.lstat()
                size = metadata.st_size
                mode = f"{metadata.st_mode & 0o7777:04o}"
            except OSError:
                size, mode = 0, "0000"
            file_records.append({
                "path": raw_path,
                "sha256": hashes.get(raw_path, ""),
                "size": size,
                "mode": mode,
                "xattr_set": _getxattr(path, XATTR_PKG) == package,
            })
            if _under(path, cfg.desktop_root):
                desktop_files.append(raw_path)
        database_entry = _normalize_manifest(package, {
            "schema_version": DB_SCHEMA_VERSION,
            "name": package,
            "version": version,
            "architecture": architecture,
            "source_package": control.get("Source", package).split()[0],
            "source_deb": str(source),
            "source_url": source_arg if source_arg.startswith(("http://", "https://")) else "",
            "sha256": source_fingerprint,
            "mode": cfg.mode,
            "installed_at": installed_at,
            "installed_by": getpass.getuser(),
            "app_dir": str(final_app),
            "files": file_records,
            "desktop_files": desktop_files,
            "mime_types": [],
            "exec_path": str(launcher) if launcher else "",
            "license": license_values,
            "debian_deps": debian_deps,
            "arch_deps": arch_deps,
            "elf_analysis": {
                "analyzed_at": utc_now(),
                "executables": [
                    {"path": str(final_app / relative), "needed": values}
                    for relative, values in elf_analysis.items()
                ],
                "unmapped_sonames": unmapped_sonames,
            },
            "isolated_libs": [],
            "sandbox": {"enabled": sandbox_requested,
                        "runtime": "firejail-or-bwrap" if sandbox_requested else None},
            "snapshot": None,
            "audit": [{"action": "install", "at": installed_at}],
        })
        for repo_file in (data_dir / "etc/apt/sources.list", data_dir / "etc/apt/sources.list.d"):
            sources = [repo_file] if repo_file.is_file() else (
                list(repo_file.glob("*.list")) if repo_file.is_dir() else [])
            if sources:
                repos = load_repos(cfg)
                for source_file in sources:
                    for item in parse_apt_sources_file(source_file):
                        repo_name = f"auto-{package}-{len(repos) + 1}"
                        repos[repo_name] = {**item, "added_by": package, "added_at": utc_now()}
                save_repos(cfg, repos)
        write_json(cfg.pkgs_dir / package / "manifest.json", database_entry)
        if cfg.mode == "user":
            set_user_owner(cfg.pkgs_dir / package, recursive=True)
        prior_db[package] = database_entry
        save_db(cfg, {package: database_entry})
        db_saved = True
        append_audit(cfg, {
            "action": "install", "package": package, "version": version,
            "mode": cfg.mode, "at": installed_at,
        })
        for message in run_hook(cfg, "on_post_install", plugin_context):
            warn(message)
        if getattr(args, "snapshot", False):
            snapshot_id = create_snapshot(cfg, package, database_entry)
            prior_db[package] = database_entry
            write_json(cfg.pkgs_dir / package / "manifest.json", database_entry)
            if cfg.mode == "user":
                set_user_owner(cfg.pkgs_dir / package, recursive=True)
            save_db(cfg, {package: database_entry})
            append_audit(cfg, {"action": "snapshot", "package": package,
                               "snapshot": snapshot_id, "at": utc_now()})
        for command in (["update-desktop-database", str(cfg.desktop_root)],
                        ["gtk-update-icon-cache", "-f", "-t", str(cfg.icons_root)]):
            try:
                subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass
        ok(f"Installed {package} {version}.")
        if launcher:
            print(f"Run: {launcher}")
        print(f"Remove: debark remove {package}")
    except Exception:
        if app_stage and app_stage.exists():
            shutil.rmtree(app_stage, ignore_errors=True)
        if app_created and final_app and final_app.exists():
            shutil.rmtree(final_app, ignore_errors=True)
        for output in created_outputs:
            output.unlink(missing_ok=True)
        if db_saved and package:
            prior_db.pop(package, None)
            try:
                save_db(cfg, {}, delete={package})
            except DebArkError:
                pass
        if package:
            (cfg.pkgs_dir / package / "manifest.json").unlink(missing_ok=True)
            try:
                (cfg.pkgs_dir / package).rmdir()
            except OSError:
                pass
        raise
    finally:
        temp_owner.cleanup()

def _under(path: Path, root: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(Path(os.path.abspath(root)))
        return True
    except ValueError:
        return False

def cmd_remove(args: argparse.Namespace, cfg: Config) -> None:
    if cfg.mode == "system" and os.geteuid() != 0:
        fail("System remove requires root. Use --user for a user package.")
    cfg.ensure()
    database = load_db(cfg)
    package = args.package
    if not PACKAGE_NAME.fullmatch(package):
        fail("Invalid package name.")
    entry = database.get(package)
    if not entry:
        fail(f"Not installed via DebArk: {package}")
    app_dir = Path(entry.get("app_dir", ""))
    expected_app_dir = cfg.apps_root / package
    if os.path.abspath(app_dir) != os.path.abspath(expected_app_dir):
        fail("Database contains an invalid application path; refusing removal.")
    if app_dir.is_symlink():
        fail("Application path is a symlink; refusing removal.")
    file_records = manifest_files(entry)
    files = [Path(record["path"]) for record in file_records]
    print(f"Package: {package}\nVersion: {entry.get('version', '?')}\nManaged files: {len(files)}")
    if not confirm(f"Remove {package}?", args.yes or cfg.auto_yes):
        warn("Aborted.")
        return
    plugin_context = {"package": package, "version": entry.get("version", "0"),
                      "mode": cfg.mode, "app_dir": str(app_dir)}
    plugin_errors = run_hook(cfg, "on_pre_remove", plugin_context)
    if plugin_errors:
        for message in plugin_errors:
            warn(message)
        fail("Removal stopped because an enabled pre-remove hook failed.")
    removed = 0
    kept = 0
    for record, path in zip(file_records, files):
        inside_app = _under(path, app_dir) and _under(path.parent.resolve(), app_dir.resolve())
        inside_bin = _under(path, cfg.bin_dir) and _under(path.parent.resolve(), cfg.bin_dir.resolve())
        inside_desktop = _under(path, cfg.desktop_root) and _under(path.parent.resolve(), cfg.desktop_root.resolve())
        if not (inside_app or inside_bin or inside_desktop):
            warn(f"Skipped path outside managed directories: {path}")
            kept += 1
            continue
        if not (path.exists() or path.is_symlink()):
            continue
        expected = record.get("sha256", "")
        actual = path_digest(path)
        if not expected or actual != expected:
            warn(f"Keeping modified file: {path}")
            kept += 1
            continue
        try:
            if path.is_dir() and not path.is_symlink():
                continue
            path.unlink()
            removed += 1
        except OSError as exc:
            warn(f"Could not remove {path}: {exc}")
            kept += 1
    if app_dir.exists():
        candidates = sorted((p for p in app_dir.rglob("*") if p.is_dir() and not p.is_symlink()),
                            key=lambda p: len(p.parts), reverse=True)
        for directory in candidates:
            try:
                directory.rmdir()
            except OSError:
                pass
    try:
        app_dir.rmdir()
    except OSError:
        pass
    del database[package]
    save_db(cfg, {}, delete={package})
    repos = load_repos(cfg)
    filtered = {name: value for name, value in repos.items()
                if value.get("added_by") != package}
    if len(filtered) != len(repos):
        save_repos(cfg, filtered)
    manifest = cfg.pkgs_dir / package / "manifest.json"
    manifest.unlink(missing_ok=True)
    try:
        manifest.parent.rmdir()
    except OSError:
        pass
    try:
        append_audit(cfg, {"action": "remove", "package": package, "at": utc_now()})
    except DebArkError as exc:
        warn(str(exc))
    for message in run_hook(cfg, "on_post_remove", plugin_context):
        warn(message)
    ok(f"Removed {removed} unchanged file(s)." + (f" Kept {kept} modified or unsafe path(s)." if kept else ""))

def cmd_list(args: argparse.Namespace, cfg: Config) -> None:
    database = load_db(cfg)
    if _OUTPUT_JSON:
        print(json.dumps({
            "success": True,
            "command": "list",
            "maintainer": AUTHOR,
            "repository": REPO,
            "mode": cfg.mode,
            "packages": [
                {"name": name, "version": entry.get("version", "?"),
                 "installed_at": entry.get("installed_at", "")}
                for name, entry in sorted(database.items())
            ],
        }, ensure_ascii=False))
        return
    if not database:
        print(f"No packages installed via DebArk ({cfg.mode} mode).")
        return
    header(f"Installed packages ({cfg.mode})")
    print(f"{'Package':<28} {'Version':<24} {'Installed'}")
    for name, entry in sorted(database.items()):
        installed = str(entry.get("installed_at", ""))[:19].replace("T", " ")
        print(f"{name:<28} {entry.get('version', '?'):<24} {installed}")

def cmd_info(args: argparse.Namespace, cfg: Config) -> None:
    candidate = Path(args.deb).expanduser()
    if not candidate.is_file():
        if PACKAGE_NAME.fullmatch(args.deb):
            entry = load_db(cfg).get(args.deb)
            if entry:
                _print_installed_info(args.deb, entry)
                return
        fail(f"Package file or installed DebArk package not found: {candidate}")
    path = candidate.resolve()
    with tempfile.TemporaryDirectory(prefix="debark-info-") as temp_name:
        control, data = extract_deb(path, Path(temp_name))
        package = control.get("Package", path.stem)
        header(f"Package information: {path.name}")
        for key in ("Package", "Version", "Architecture", "Maintainer"):
            print(f"{key}: {control.get(key, '?')}")
        print(f"Size: {fmt_size(path.stat().st_size)}")
        print(f"SHA256: {path_digest(path)}")
        licenses = detect_license(control, data, package)
        print(f"Declared license: {'; '.join(licenses) if licenses else 'not found'}")
        description = control.get("Description", "").splitlines()
        print(f"Description: {description[0] if description else '-'}")
        main = find_main_executable(data, package)
        print(f"Main executable: {main.relative_to(data) if main else '-'}")
        print(f"Icons: {len(find_icons(data))}")
        print(f"Desktop files: {len(list(data.rglob('*.desktop')))}")
        section("Dependencies")
        for dependency in parse_dep_list(control.get("Depends", "")):
            print(f"  {dependency} → {map_dep(dependency, cfg) or 'unmapped'}")

def cmd_search(args: argparse.Namespace, cfg: Config) -> None:
    hits = [(name, entry) for name, entry in load_db(cfg).items()
            if args.query.lower() in name.lower()]
    if not hits:
        print(f"No matches for {args.query!r}.")
        return
    for name, entry in sorted(hits):
        print(f"{name}\t{entry.get('version', '?')}")


def _print_installed_info(package: str, entry: dict[str, Any]) -> None:
    header(f"Installed package: {package}")
    print(f"Version: {entry.get('version', '?')}")
    print(f"Architecture: {entry.get('architecture', '?')}")
    print(f"Installed: {entry.get('installed_at', '?')}")
    print(f"Source: {entry.get('source_url') or entry.get('source_deb') or '?'}")
    licenses = entry.get("license", [])
    if isinstance(licenses, str):
        licenses = [licenses]
    print(f"License: {'; '.join(str(item) for item in licenses) if licenses else '?'}")
    files = manifest_files(entry)
    print(f"Managed files: {len(files)}")
    arch_deps = entry.get("arch_deps", [])
    if isinstance(arch_deps, list):
        print(f"Arch dependencies: {', '.join(str(item) for item in arch_deps) or 'none recorded'}")
    if entry.get("apt_repository"):
        print(f"APT repository: {entry['apt_repository']} ({entry.get('apt_suite', '?')})")

def cmd_files(args: argparse.Namespace, cfg: Config) -> None:
    entry = load_db(cfg).get(args.package)
    if not entry:
        fail(f"Not installed via DebArk: {args.package}")
    for record in manifest_files(entry):
        print(record["path"])

def cmd_verify(args: argparse.Namespace, cfg: Config) -> None:
    entry = load_db(cfg).get(args.package)
    if not entry:
        fail(f"Not installed via DebArk: {args.package}")
    issues = []
    records = manifest_files(entry)
    for record in records:
        raw_path = record["path"]
        path = Path(raw_path)
        if not path.exists() and not path.is_symlink():
            issues.append((path, "missing"))
        elif path_digest(path) != record.get("sha256", ""):
            issues.append((path, "hash mismatch"))
    if not issues:
        ok(f"{args.package}: {len(records)} managed file(s) verified.")
    else:
        warn(f"{args.package}: {len(issues)} issue(s) found.")
        for path, reason in issues[:20]:
            print(f"  {path} ({reason})")
        fail(f"Verification failed for {len(issues)} managed file(s).")

def cmd_repair(args: argparse.Namespace, cfg: Config) -> None:
    if cfg.mode == "system" and os.geteuid() != 0:
        fail("System repair requires root. Use --user for a user package.")
    cfg.ensure()
    if not PACKAGE_NAME.fullmatch(args.package):
        fail("Invalid package name.")
    database = load_db(cfg)
    entry = database.get(args.package)
    if not entry:
        fail(f"Not installed via DebArk: {args.package}")
    app_dir = cfg.apps_root / args.package
    if Path(entry.get("app_dir", "")).absolute() != app_dir.absolute() or app_dir.is_symlink():
        fail("Database contains an invalid application path; refusing repair.")

    source = Path(entry.get("source_deb", "")).expanduser()
    if not source.is_file() and entry.get("source_url"):
        filename = Path(str(entry["source_url"]).split("?", 1)[0].rstrip("/")).name
        source = download(str(entry["source_url"]), cfg.cache_dir / (filename or f"{args.package}.deb"),
                         user_mode=cfg.mode == "user", cfg=cfg)
    if not source.is_file():
        fail("The original .deb is unavailable; restore it to the recorded path or reinstall from its source URL.")
    expected_source_hash = entry.get("sha256", "")
    if expected_source_hash and path_digest(source) != expected_source_hash:
        fail("The saved .deb does not match the SHA256 recorded at installation.")

    broken: list[dict[str, Any]] = []
    skipped: list[str] = []
    for record in manifest_files(entry):
        target = Path(record["path"])
        expected = record.get("sha256", "")
        if target.exists() or target.is_symlink():
            if expected and path_digest(target) == expected:
                continue
        if not _under(target, app_dir):
            skipped.append(str(target))
            continue
        broken.append(record)
    if not broken:
        if skipped:
            warn(f"{len(skipped)} launcher or integration file(s) need manual review.")
        ok(f"{args.package}: no repairable payload files found.")
        return
    print(f"Package: {args.package}\nFiles to restore: {len(broken)}")
    if skipped:
        warn(f"Skipping {len(skipped)} managed file(s) outside the application payload.")
    if not confirm(f"Restore {len(broken)} file(s) from {source.name}?", args.yes or cfg.auto_yes):
        warn("Aborted.")
        return

    successes: list[dict[str, Any]] = []
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="debark-repair-") as temp_name:
        try:
            control, payload = extract_deb(source, Path(temp_name))
        except DebArkError:
            raise
        if control.get("Package", "").lower() != args.package:
            fail("The saved .deb contains a different package; refusing repair.")
        for index, record in enumerate(broken):
            target = Path(record["path"])
            try:
                relative = target.relative_to(app_dir)
                source_file = payload / relative
                if not (source_file.exists() or source_file.is_symlink()):
                    raise DebArkError(f"Source package is missing {relative}.")
                current = app_dir
                for part in relative.parts[:-1]:
                    current = current / part
                    if current.is_symlink():
                        raise DebArkError(f"Refusing to repair through a symlink: {current}")
                if path_digest(source_file) != record.get("sha256", ""):
                    raise DebArkError(f"Source hash differs from the installed manifest for {relative}.")
                target.parent.mkdir(parents=True, exist_ok=True)
                stage = target.parent / f".debark-repair-{os.getpid()}-{index}"
                if stage.exists() or stage.is_symlink():
                    raise DebArkError(f"Repair staging path already exists: {stage}")
                if source_file.is_symlink():
                    os.symlink(os.readlink(source_file), stage)
                else:
                    shutil.copy2(source_file, stage, follow_symlinks=False)
                os.replace(stage, target)
                digest = path_digest(target)
                mark_file(target, args.package, digest, str(entry.get("version", "")))
                metadata = target.lstat()
                record.update({
                    "sha256": digest,
                    "size": metadata.st_size,
                    "mode": f"{metadata.st_mode & 0o7777:04o}",
                    "xattr_set": _getxattr(target, XATTR_PKG) == args.package,
                })
                successes.append(record)
            except (OSError, ValueError, DebArkError) as exc:
                errors.append(str(exc))

    if successes:
        entry["files"] = manifest_files(entry)
        entry.setdefault("audit", []).append({"action": "repair", "at": utc_now()})
        write_json(cfg.pkgs_dir / args.package / "manifest.json", entry)
        if cfg.mode == "user":
            set_user_owner(cfg.pkgs_dir / args.package, recursive=True)
        database[args.package] = entry
        save_db(cfg, {args.package: entry})
        append_audit(cfg, {
            "action": "repair", "package": args.package,
            "files_restored": len(successes), "at": utc_now(),
        })
        ok(f"Restored {len(successes)} file(s).")
    for error in errors:
        warn(error)
    if errors:
        fail(f"Repair completed with {len(errors)} error(s).")

def cmd_scan(args: argparse.Namespace, cfg: Config) -> None:
    found: dict[str, int] = {}
    for root in (cfg.apps_root, cfg.bin_dir, cfg.desktop_root):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() or path.is_symlink():
                name = _getxattr(path, XATTR_PKG)
                if name:
                    found[name] = found.get(name, 0) + 1
    if not found:
        print("No DebArk markers found under the application directory.")
    else:
        for package, count in sorted(found.items()):
            print(f"{package}: {count} file(s)")


def _load_repo_index(cfg: Config, repos: dict[str, Any], name: str) -> dict[str, Any]:
    definition = repos.get(name)
    if not isinstance(definition, dict):
        raise experimental.ExperimentalError(f"Repository not found: {name}")
    index = experimental.load_synced_packages(cfg.cache_dir, name)
    configured_url = str(definition.get("url", "")).rstrip("/")
    if (index.get("url") != configured_url
            or index.get("suite") != definition.get("suite")):
        raise experimental.ExperimentalError(
            f"Cached index for {name} does not match its current settings; run debark repo sync {name}."
        )
    return index


def _load_repo_indexes(
    cfg: Config, repos: dict[str, Any], name: str | None = None
) -> dict[str, dict[str, Any]]:
    if name is not None and name not in repos:
        fail(f"Repository not found: {name}")
    selected = [name] if name else sorted(repos)
    if not selected:
        fail("No Debian repositories are registered.")
    indexes: dict[str, dict[str, Any]] = {}
    for repo_name in selected:
        try:
            indexes[repo_name] = _load_repo_index(cfg, repos, repo_name)
        except experimental.ExperimentalError as exc:
            warn(str(exc))
    if not indexes:
        fail("No usable synced repository indexes. Add a repository and run `debark repo sync` first.")
    return indexes


def _read_upstream_file(url: str, limit: int = 512 * 1024) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": f"DebArk/{VERSION} ({REPO})", "Accept": "text/plain"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise DebArkError("The update metadata is larger than expected.")
    return payload.decode("utf-8")


def _version_key(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        raise DebArkError(f"Unsupported DebArk version format: {value}")
    return tuple(int(part) for part in value.split("."))


def _upstream_release_notes(changelog: str, version: str) -> list[str]:
    heading = re.compile(
        rf"^##\s+\[?v?{re.escape(version)}\]?(?:\s|$).*?$", re.MULTILINE
    )
    match = heading.search(changelog)
    if match is None:
        return []
    following = re.search(r"^##\s+", changelog[match.end():], re.MULTILINE)
    body = changelog[match.end():match.end() + following.start()
                     if following else len(changelog)]
    return [line[2:].strip() for line in body.splitlines()
            if line.startswith(("- ", "* "))]


def _install_upstream_debark(cfg: Config) -> None:
    if _OUTPUT_JSON:
        info("Rerun this command without --json to review and approve the DebArk update.")
        return
    if not sys.stdin.isatty():
        info("Run `debark update` in a terminal to approve and install this update.")
        return
    if not _ask_yes_no("Install the DebArk update now?", False):
        info("DebArk update skipped.")
        return

    installer_url = "https://raw.githubusercontent.com/Mohammad-Nicke/debark/main/install.sh"
    try:
        installer_text = _read_upstream_file(installer_url, limit=256 * 1024)
        if "DEBARK_SELF_UPDATE" not in installer_text or "Mohammad-Nicke/debark" not in installer_text:
            raise DebArkError("The downloaded installer did not pass its project identity check.")
        bash = shutil.which("bash")
        if not bash:
            raise DebArkError("Bash is required to run the DebArk installer.")
        with tempfile.TemporaryDirectory(prefix="debark-update-") as temporary:
            installer = Path(temporary) / "install.sh"
            installer.write_text(installer_text, encoding="utf-8")
            os.chmod(installer, 0o700)
            environment = os.environ.copy()
            environment["DEBARK_INSTALL_MODE"] = cfg.mode
            environment["DEBARK_SELF_UPDATE"] = "1"
            result = subprocess.run([bash, str(installer)], env=environment, check=False)
        if result.returncode != 0:
            raise DebArkError(f"The DebArk installer exited with status {result.returncode}.")
    except (OSError, UnicodeError, urllib.error.URLError) as exc:
        raise DebArkError(f"Could not download or start the DebArk update: {exc}") from exc


def check_debark_update(cfg: Config) -> None:
    """Check the current GitHub source and offer a same-mode in-place update."""
    base = "https://raw.githubusercontent.com/Mohammad-Nicke/debark/main"
    try:
        init_source = _read_upstream_file(f"{base}/src/debark/__init__.py", limit=64 * 1024)
        match = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]\s*$",
                          init_source, re.MULTILINE)
        if match is None:
            raise DebArkError("The upstream source does not declare a DebArk version.")
        latest = match.group(1)
        if _version_key(latest) <= _version_key(VERSION):
            return
        changelog = _read_upstream_file(f"{base}/CHANGELOG.md")
    except (OSError, UnicodeError, ValueError, urllib.error.URLError, DebArkError) as exc:
        warn(f"Could not check for DebArk updates: {exc}")
        return

    info(f"DebArk {latest} is available (installed: {VERSION}).")
    notes = _upstream_release_notes(changelog, latest)
    if notes:
        section(f"What's new in DebArk {latest}")
        for note in notes[:12]:
            print(f"  {UI.color('•', '32')} {note}")
    else:
        info("No release notes were found for this version.")
    _install_upstream_debark(cfg)

def cmd_update(args: argparse.Namespace, cfg: Config) -> None:
    cfg.ensure()
    write_json(cfg.depmap_file, BUILTIN_DEPMAP)
    if cfg.mode == "user":
        set_user_owner(cfg.depmap_file)
    ok(f"Saved {len(BUILTIN_DEPMAP)} built-in dependency mappings.")
    if cfg.mode == "system" and shutil.which("pacman"):
        result = subprocess.run(["pacman", "-Fy", "--noconfirm"], check=False)
        if result.returncode != 0:
            warn("Could not refresh the pacman file database; SONAME lookup may be incomplete.")
    repos = load_repos(cfg)
    if repos:
        print(f"{len(repos)} Debian source definition(s) are registered.")
        print("Use `debark repo sync NAME` to fetch and verify beta APT indexes.")
    check_debark_update(cfg)

def cmd_upgrade(args: argparse.Namespace, cfg: Config) -> None:
    count = len(load_db(cfg))
    pins = load_pins(cfg)
    print(f"{count} DebArk package(s) are registered.")
    if pins:
        print("Pinned versions:")
        for package, version in sorted(pins.items()):
            print(f"  {package}: {version}")
    if cfg.experimental_features:
        repos = load_repos(cfg)
        found = False
        for package, entry in load_db(cfg).items():
            repo_name = entry.get("apt_repository")
            if not isinstance(repo_name, str) or repo_name not in repos:
                continue
            try:
                index = _load_repo_index(cfg, repos, repo_name)
                candidate = experimental.newest_package(index["packages"], package)
                if candidate and experimental.compare_debian_versions(
                    candidate["Version"], str(entry.get("version", "0"))
                ) > 0:
                    print(f"{package}: {entry.get('version', '?')} → {candidate['Version']} ({repo_name})")
                    found = True
            except experimental.ExperimentalError as exc:
                warn(str(exc))
        if found:
            print("Beta check only: package replacement is not enabled in this release.")
        elif not any(entry.get("apt_repository") for entry in load_db(cfg).values()):
            print("No APT-managed DebArk packages are registered for update checks.")
    else:
        print("Automatic upgrades are unavailable; install a newer local package or direct URL explicitly.")
    check_debark_update(cfg)


def _running_from_user_install() -> bool:
    """Select per-user state automatically for the per-user installed command."""
    candidate = shutil.which(sys.argv[0]) or sys.argv[0]
    invoked_path = Path(candidate).expanduser().absolute()
    return invoked_path == current_user_home() / ".local/bin/debark"


def _require_experimental(cfg: Config) -> None:
    if not cfg.experimental_features:
        fail("Experimental integrations are disabled. Enable them from the installer or with `debark config experimental_features true`.")


def cmd_cve(args: argparse.Namespace, cfg: Config) -> None:
    _require_experimental(cfg)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", args.suite):
        fail("Provide a valid Debian suite, such as bookworm or trixie.")
    try:
        tracker = experimental.fetch_tracker(cfg.cache_dir / "debian-security-tracker.json")
        known_suites = experimental.tracker_suites(tracker)
        if args.suite not in known_suites:
            examples = ", ".join(sorted(known_suites)[:12])
            suffix = f" Known suites in the current tracker data: {examples}." if examples else ""
            fail(f"No tracker data is published for suite {args.suite!r}; check the suite name.{suffix}")
        findings = experimental.package_cves(load_db(cfg), tracker, args.suite)
    except experimental.ExperimentalError as exc:
        fail(str(exc))
    if not findings:
        print(f"No matching Debian Security Tracker advisories were found for suite {args.suite}.")
        return
    for finding in findings:
        fixed = f"; fixed in {finding['fixed_version']}" if finding["fixed_version"] else ""
        print(f"{finding['package']} {finding['version']}: {finding['cve']} "
              f"({finding['status']}{fixed})")
        if finding["description"]:
            print(f"  {finding['description']}")
    print("Advisories are best-effort. Confirm each result with Debian's tracker before acting.")

def cmd_pin(args: argparse.Namespace, cfg: Config) -> None:
    pins = load_pins(cfg)
    if args.package is None:
        if not pins:
            print("No package versions are pinned.")
        else:
            for package, version in sorted(pins.items()):
                print(f"{package}\t{version}")
        return
    if not PACKAGE_NAME.fullmatch(args.package):
        fail("Invalid package name.")
    if args.clear:
        if args.package not in pins:
            fail(f"No version pin exists for {args.package}.")
        del pins[args.package]
    else:
        if not args.version:
            fail("Provide a version or use --clear.")
        pins[args.package] = args.version
    cfg.ensure()
    write_json(cfg.pins_file, pins)
    if cfg.mode == "user":
        set_user_owner(cfg.pins_file)
    ok(f"{'Cleared' if args.clear else 'Pinned'} {args.package}" +
       ("." if args.clear else f" to version {args.version}."))


def _clear_repo_cache(cfg: Config, name: str) -> None:
    if name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", name):
        fail("Invalid repository name; refusing to clear its cache.")
    cache_path = cfg.cache_dir / "apt" / name
    apt_cache = cache_path.parent
    if apt_cache.is_symlink():
        fail(f"Refusing to use a symlinked repository cache directory: {apt_cache}")
    if apt_cache.exists() and not apt_cache.is_dir():
        fail(f"Refusing to use a non-directory repository cache path: {apt_cache}")
    if cache_path.is_symlink():
        fail(f"Refusing to remove a symlinked repository cache: {cache_path}")
    if cache_path.exists():
        if not cache_path.is_dir():
            fail(f"Refusing to remove a non-directory repository cache: {cache_path}")
        shutil.rmtree(cache_path)


def cmd_repo(args: argparse.Namespace, cfg: Config) -> None:
    repos = load_repos(cfg)
    if args.repo_command == "list":
        if not repos:
            print("No Debian repositories registered.")
        for name, item in sorted(repos.items()):
            print(f"{name}\n  {item.get('url', '')} {item.get('suite', '')} "
                  f"{' '.join(item.get('components', []))}")
    elif args.repo_command == "search":
        _require_experimental(cfg)
        query = args.query.strip().casefold()
        if not query.strip():
            fail("Provide a package name or description fragment to search for.")
        indexes = _load_repo_indexes(cfg, repos, args.repo)
        matches: list[tuple[str, dict[str, str]]] = []
        for repo_name, index in indexes.items():
            packages: dict[str, list[dict[str, str]]] = {}
            for entry in index["packages"]:
                package_name = str(entry.get("Package", ""))
                description = " ".join(str(entry.get("Description", "")).splitlines())
                if query in package_name.casefold() or query in description.casefold():
                    packages.setdefault(package_name, []).append(entry)
            for package_name, versions in packages.items():
                candidate = experimental.newest_package(versions, package_name)
                if candidate:
                    matches.append((repo_name, candidate))
        if not matches:
            print(f"No matches for {args.query!r} in the usable synced indexes.")
            return
        for repo_name, entry in sorted(matches, key=lambda item: (item[1]["Package"], item[0])):
            description = " ".join(str(entry.get("Description", "")).splitlines())
            print(f"{entry['Package']:<32} {entry['Version']:<24} [{repo_name}]")
            if description:
                print(f"  {description[:180]}")
    elif args.repo_command == "info":
        _require_experimental(cfg)
        if not PACKAGE_NAME.fullmatch(args.package):
            fail("Invalid Debian package name.")
        indexes = _load_repo_indexes(cfg, repos, args.repo)
        matches: list[tuple[str, dict[str, str]]] = []
        for repo_name, index in indexes.items():
            candidate = experimental.newest_package(index["packages"], args.package)
            if candidate:
                matches.append((repo_name, candidate))
        if not matches:
            print(f"Package {args.package} was not found in the usable synced indexes.")
            return
        for repo_name, entry in sorted(matches, key=lambda item: item[0]):
            header(f"{entry['Package']} {entry['Version']} · {repo_name}")
            print(f"Architecture: {entry.get('Architecture', '?')}")
            try:
                size = fmt_size(int(entry.get("Size", "0")))
            except (TypeError, ValueError):
                size = "?"
            print(f"Size: {size}")
            print(f"Filename: {entry.get('Filename', '?')}")
            print(f"SHA256: {entry.get('SHA256', '?')}")
            if entry.get("Source"):
                print(f"Source package: {entry['Source']}")
            if entry.get("Depends"):
                print(f"Depends: {entry['Depends']}")
            if entry.get("Description"):
                print(f"Description: {' '.join(entry['Description'].splitlines())}")
    elif args.repo_command == "add":
        if args.name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", args.name):
            fail("Invalid repository name.")
        if not args.url.startswith("https://") or "@" in args.url.split("//", 1)[-1].split("/", 1)[0]:
            fail("Experimental APT repositories must use credential-free HTTPS URLs.")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./_-]{0,79}", args.suite):
            fail("Invalid Debian suite name.")
        if any(part in (".", "..") for part in Path(args.suite).parts):
            fail("Invalid Debian suite path.")
        if any(not re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", item) for item in args.components):
            fail("Invalid Debian repository component.")
        item = {"url": args.url.rstrip("/"), "suite": args.suite,
                "components": args.components, "added_at": utc_now()}
        if args.keyring:
            keyring = Path(args.keyring).expanduser()
            if keyring.is_symlink() or not keyring.is_file():
                fail("The trusted keyring must be an existing regular file.")
            item["keyring"] = str(keyring.resolve())
        previous = repos.get(args.name)
        previous_settings = None
        if isinstance(previous, dict):
            previous_settings = tuple(
                previous.get(key) for key in ("url", "suite", "components", "keyring")
            )
        new_settings = tuple(item.get(key) for key in
                             ("url", "suite", "components", "keyring"))
        repos[args.name] = item
        save_repos(cfg, repos)
        if previous_settings is not None and previous_settings != new_settings:
            _clear_repo_cache(cfg, args.name)
        ok(f"Added repository {args.name}.")
    elif args.repo_command == "remove":
        if args.name not in repos:
            fail(f"Repository not found: {args.name}")
        del repos[args.name]
        save_repos(cfg, repos)
        _clear_repo_cache(cfg, args.name)
        ok(f"Removed repository {args.name}.")
    elif args.repo_command == "sync":
        _require_experimental(cfg)
        selected = [args.name] if args.name else sorted(repos)
        if not selected:
            fail("No Debian repositories are registered.")
        cfg.ensure()
        for name in selected:
            if name not in repos:
                fail(f"Repository not found: {name}")
            try:
                result = experimental.sync_repository(
                    {"name": name, **repos[name]}, cfg.cache_dir
                )
            except experimental.ExperimentalError as exc:
                fail(str(exc))
            ok(f"Verified {result['package_count']} package entries from {name} ({result['suite']}).")
    elif args.repo_command == "install":
        _require_experimental(cfg)
        if not PACKAGE_NAME.fullmatch(args.package):
            fail("Invalid Debian package name.")
        if args.package in load_db(cfg):
            fail("Updating an already-installed package is not available in the experimental preview yet.")
        if args.name not in repos:
            fail(f"Repository not found: {args.name}")
        try:
            index = _load_repo_index(cfg, repos, args.name)
            candidate = experimental.newest_package(index["packages"], args.package, args.version)
            if candidate is None:
                fail(f"Package {args.package} was not found in the verified index.")
            package_path = cfg.cache_dir / "apt" / args.name / "packages" / (
                f"{args.package}-{re.sub(r'[^A-Za-z0-9.+:~_-]', '_', candidate['Version'])}.deb"
            )
            experimental.download_package(index["url"], candidate, package_path)
        except experimental.ExperimentalError as exc:
            fail(str(exc))
        install_args = argparse.Namespace(
            deb=str(package_path), dry_run=False, no_deps=args.no_deps, yes=args.yes,
            expected_sha256=candidate["SHA256"], sha256="", gpg_signature=None,
            keyring=None, requested_version=candidate["Version"], sandbox=args.sandbox,
            snapshot=False, verify_after=True,
        )
        cmd_install(install_args, cfg)
        database = load_db(cfg)
        entry = database.get(args.package)
        if entry:
            entry["apt_repository"] = args.name
            entry["apt_suite"] = repos[args.name]["suite"]
            entry["source_package"] = str(candidate.get("Source", args.package)).split()[0]
            entry["source_url"] = f"{index['url']}/{candidate['Filename']}"
            write_json(cfg.pkgs_dir / args.package / "manifest.json", entry)
            save_db(cfg, {args.package: entry})
            if cfg.mode == "user":
                set_user_owner(cfg.pkgs_dir / args.package, recursive=True)
    else:
        print("Use: debark repo add, remove, list, sync, or install")

def cmd_config(args: argparse.Namespace, cfg: Config) -> None:
    allowed = {"auto_yes", "colors", "threads", "experimental_features"}
    if args.key is not None:
        if args.key not in allowed or args.value is None:
            fail("Set one of: auto_yes, colors, threads, experimental_features.")
        value: Any = args.value
        if args.key == "threads":
            try:
                value = max(1, min(16, int(value)))
            except ValueError as exc:
                raise DebArkError("threads must be an integer from 1 to 16.") from exc
        else:
            if value.lower() not in ("true", "false"):
                fail(f"{args.key} must be true or false.")
            value = value.lower() == "true"
        cfg.save({args.key: value})
        ok(f"Set {args.key} = {value}")
        return
    for key, value in (("mode", cfg.mode), ("config", cfg.cfg_file), ("data", cfg.data_dir),
                       ("apps", cfg.apps_root), ("bin", cfg.bin_dir),
                       ("auto_yes", cfg.auto_yes), ("colors", cfg.colors),
                       ("experimental_features", cfg.experimental_features),
                       ("threads", cfg.threads)):
        print(f"{key}: {value}")

def cmd_profile(args: argparse.Namespace, cfg: Config) -> None:
    cfg.ensure()
    if args.profile_command == "list":
        profiles = sorted(path.stem for path in cfg.profiles_dir.glob("*.json") if path.is_file())
        if not profiles:
            print("No saved profiles.")
            return
        for name in profiles:
            print(name)
        return
    name = args.name
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", name):
        fail("Invalid profile name.")
    path = cfg.profiles_dir / f"{name}.json"
    if args.profile_command == "remove":
        if not path.is_file() or path.is_symlink():
            fail(f"Profile not found: {name}")
        path.unlink()
        ok(f"Removed profile {name}.")
        return
    write_json(path, {
        "managed_by": "DebArk", "maintainer": AUTHOR, "repository": REPO,
        "mode": cfg.mode, "auto_yes": cfg.auto_yes, "snapshot": False,
        "sandbox": False, "verify": True, "threads": cfg.threads,
    })
    if cfg.mode == "user":
        set_user_owner(path)
    ok(f"Saved profile {name} to {path}.")

def cmd_plugin(args: argparse.Namespace, cfg: Config) -> None:
    directory = cfg.cfg_dir / "plugins"
    if args.plugin_command == "list":
        enabled = set(enabled_plugins(cfg))
        files = sorted(path.stem for path in directory.glob("*.py")
                       if path.is_file() and not path.is_symlink()) if directory.is_dir() else []
        if not files:
            print("No local plugins found.")
            return
        for name in files:
            print(f"{name}\t{'enabled' if name in enabled else 'disabled'}")
        return
    name = args.name
    path = directory / f"{name}.py"
    try:
        set_plugin_enabled(cfg, name, args.plugin_command == "enable")
    except ValueError as exc:
        raise DebArkError(str(exc)) from exc
    if args.plugin_command == "enable" and (not path.is_file() or path.is_symlink()):
        set_plugin_enabled(cfg, name, False)
        fail(f"Plugin source file not found: {path}")
    ok(f"Plugin {name} {'enabled' if args.plugin_command == 'enable' else 'disabled'}.")

def cmd_doctor(args: argparse.Namespace, cfg: Config) -> None:
    header("DebArk environment")
    for name in ("python3", "ar", "bsdtar", "pacman", "readelf", "gpgv",
                 "firejail", "bwrap", "update-desktop-database", "gtk-update-icon-cache"):
        executable = shutil.which(name)
        status = f"found: {executable}" if executable else "not found"
        print(f"{name}: {status}")
    print(f"Mode: {cfg.mode}")
    print(f"Architecture: {platform.machine()}")
    try:
        model_bytes = (Path(__file__).with_name("data") / "dependency-ranker.json").stat().st_size
        print(f"Offline resolver model: {fmt_size(model_bytes)} (maximum 20 MiB)")
    except OSError:
        print("Offline resolver model: not available")
    try:
        with tempfile.TemporaryDirectory(prefix="debark-doctor-") as temp:
            test = Path(temp) / "xattr-check"
            test.write_text("x", encoding="ascii")
            os.setxattr(test, "user.debark.test", b"1")
        print("User xattrs: supported")
    except (OSError, AttributeError):
        print("User xattrs: unavailable; manifest hashes remain enabled")

def cmd_about(args: argparse.Namespace, cfg: Config) -> None:
    print(f"DebArk {VERSION}\nDisembark Debian packages into Arch Linux.\n"
          f"Maintainer: {AUTHOR}\nRepository: {REPO}\nMode: {cfg.mode}\nLicense: {__license__}")

def cmd_stats(args: argparse.Namespace, cfg: Config) -> None:
    database = load_db(cfg)
    file_count = 0
    byte_count = 0
    dependency_count = 0
    for entry in database.values():
        records = manifest_files(entry)
        file_count += len(records)
        dependency_count += len(entry.get("arch_deps", []))
        byte_count += sum(int(record.get("size", 0) or 0) for record in records)
    result = {
        "mode": cfg.mode,
        "maintainer": AUTHOR,
        "repository": REPO,
        "packages": len(database),
        "managed_files": file_count,
        "arch_dependencies": dependency_count,
        "managed_bytes": byte_count,
    }
    if getattr(args, "json", False):
        print(json.dumps(result, sort_keys=True))
        return
    header("DebArk statistics")
    for key, value in result.items():
        print(f"{key.replace('_', ' ').title()}: {value}")

def cmd_license(args: argparse.Namespace, cfg: Config) -> None:
    database = load_db(cfg)
    if args.package:
        entry = database.get(args.package)
        if not entry:
            fail(f"Not installed via DebArk: {args.package}")
        licenses = entry.get("license", [])
        if not licenses:
            print(f"{args.package}: no license declaration was found in the package metadata.")
            return
        print(f"{args.package}: {'; '.join(licenses)}")
        return
    if not database:
        print("No installed package license records.")
        return
    for name, entry in sorted(database.items()):
        licenses = entry.get("license", [])
        print(f"{name}\t{'; '.join(licenses) if licenses else 'unknown'}")

def cmd_log(args: argparse.Namespace, cfg: Config) -> None:
    try:
        lines = read_regular_text(cfg.audit_file).splitlines()[-50:]
    except OSError:
        lines = []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except ValueError:
            continue
    if getattr(args, "json", False):
        print(json.dumps(events, sort_keys=True))
        return
    if not events:
        print("No audit events recorded.")
        return
    for event in events:
        print(f"{event.get('at', '?')}  {event.get('action', '?')}  {event.get('package', '')}")

def cmd_gc(args: argparse.Namespace, cfg: Config) -> None:
    if cfg.mode == "system" and os.geteuid() != 0:
        fail("System marker cleanup requires root.")
    cfg.ensure()
    database = load_db(cfg)
    tracked: dict[str, set[str]] = {}
    for name, entry in database.items():
        tracked[name] = {record["path"] for record in manifest_files(entry)}
    stale: list[Path] = []
    for root in (cfg.apps_root, cfg.bin_dir, cfg.desktop_root):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not (path.is_file() or path.is_symlink()):
                continue
            package = _getxattr(path, XATTR_PKG)
            if package and (package not in tracked or str(path) not in tracked[package]):
                stale.append(path)
    if not stale:
        print("No orphaned DebArk markers found.")
        return
    if not confirm(f"Clear orphaned markers from {len(stale)} file(s)?", getattr(args, "yes", False)):
        warn("Aborted.")
        return
    cleared = 0
    for path in stale:
        for attribute in (XATTR_PKG, XATTR_HASH, XATTR_VERSION, XATTR_INSTALLED):
            try:
                os.removexattr(path, attribute, follow_symlinks=False)
            except (OSError, AttributeError, NotImplementedError):
                pass
        if _getxattr(path, XATTR_PKG) is None:
            cleared += 1
    append_audit(cfg, {"action": "gc", "markers_cleared": cleared, "at": utc_now()})
    ok(f"Cleared orphaned markers from {cleared} file(s).")

def cmd_snapshot(args: argparse.Namespace, cfg: Config) -> None:
    if cfg.mode == "system" and os.geteuid() != 0:
        fail("System snapshots require root. Use --user for a user package.")
    database = load_db(cfg)
    entry = database.get(args.package)
    if not entry:
        fail(f"Not installed via DebArk: {args.package}")
    snapshot_id = create_snapshot(cfg, args.package, entry)
    database[args.package] = entry
    write_json(cfg.pkgs_dir / args.package / "manifest.json", entry)
    if cfg.mode == "user":
        set_user_owner(cfg.pkgs_dir / args.package, recursive=True)
    save_db(cfg, {args.package: entry})
    append_audit(cfg, {"action": "snapshot", "package": args.package,
                       "snapshot": snapshot_id, "at": utc_now()})
    ok(f"Saved restore point {snapshot_id} for {args.package}.")

def cmd_rollback(args: argparse.Namespace, cfg: Config) -> None:
    if cfg.mode == "system" and os.geteuid() != 0:
        fail("System rollback requires root. Use --user for a user package.")
    cfg.ensure()
    database = load_db(cfg)
    entry = database.get(args.package)
    if not entry:
        fail(f"Not installed via DebArk: {args.package}")
    snapshots = entry.get("snapshots", [])
    if not snapshots:
        fail(f"No restore points are available for {args.package}.")
    selected = args.snapshot or snapshots[-1].get("id")
    if not any(item.get("id") == selected for item in snapshots if isinstance(item, dict)):
        fail(f"Restore point not found: {selected}")
    if not confirm(f"Restore {args.package} from {selected}?", args.yes or cfg.auto_yes):
        warn("Aborted.")
        return
    restore_snapshot(cfg, args.package, entry, selected)
    database[args.package] = entry
    save_db(cfg, {args.package: entry})
    write_json(cfg.pkgs_dir / args.package / "manifest.json", entry)
    if cfg.mode == "user":
        set_user_owner(cfg.pkgs_dir / args.package, recursive=True)
    append_audit(cfg, {"action": "rollback", "package": args.package,
                       "snapshot": selected, "at": utc_now()})
    ok(f"Restored {args.package} from {selected}.")

def cmd_extract(args: argparse.Namespace, cfg: Config) -> None:
    source = Path(args.deb).expanduser().resolve()
    if not source.is_file():
        fail(f"Package file not found: {source}")
    output = Path(args.output).expanduser().absolute() if args.output else Path.cwd() / f"{source.stem}-root"
    if output.exists() or output.is_symlink():
        fail(f"Refusing to replace existing output path: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="debark-extract-") as temp_name:
        control, payload = extract_deb(source, Path(temp_name))
        stage = output.parent / f".{output.name}.debark-stage-{os.getpid()}"
        if stage.exists() or stage.is_symlink():
            fail(f"Extraction staging path already exists: {stage}")
        try:
            shutil.copytree(payload, stage, symlinks=True)
            os.replace(stage, output)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    ok(f"Extracted {control.get('Package', source.stem)} to {output}.")

def cmd_convert(args: argparse.Namespace, cfg: Config) -> None:
    source = Path(args.deb).expanduser().resolve()
    if not source.is_file():
        fail(f"Package file not found: {source}")
    archiver = shutil.which("bsdtar")
    if not archiver:
        fail("Conversion requires bsdtar with zstd support.")
    output = Path(args.output).expanduser().absolute() if args.output else Path.cwd() / f"{source.stem}.pkg.tar.zst"
    if output.exists() or output.is_symlink():
        fail(f"Refusing to replace existing output file: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="debark-convert-") as temp_name:
        temp_root = Path(temp_name)
        control, payload = extract_deb(source, temp_root)
        package = control.get("Package", source.stem).strip().lower()
        version = control.get("Version", "0").strip()
        if not PACKAGE_NAME.fullmatch(package):
            fail(f"Invalid Debian package name: {package!r}")
        if package == "" or len(package) > 255:
            fail("Package name is too long for conversion.")
        arch_version = re.sub(r"[^A-Za-z0-9.+_]", "_", version).strip("._+") or "0"
        description = control.get("Description", "Converted Debian package").splitlines()[0]
        arch = _deb_to_arch_arch(control.get("Architecture", "any"))
        main_exec = find_main_executable(payload, package)
        payload_size = sum(path.stat().st_size for path in payload.rglob("*")
                           if path.is_file() and not path.is_symlink())
        safe_root = temp_root / "arch-root"
        app_root = safe_root / "opt" / package
        app_root.parent.mkdir(parents=True, exist_ok=True)
        os.replace(payload, app_root)
        wrapper_dir = safe_root / "usr" / "bin"
        wrapper_dir.mkdir(parents=True, exist_ok=True)
        if main_exec:
            relative = main_exec.relative_to(payload)
            wrapper = wrapper_dir / package
            wrapper.write_text(
                "#!/bin/sh\n"
                f"exec {shlex.quote(str(Path('/opt') / package / relative))} \"$@\"\n",
                encoding="utf-8",
            )
            os.chmod(wrapper, 0o755)
        metadata = [
            f"pkgname = {package}", f"pkgbase = {package}", f"pkgver = {arch_version}",
            f"pkgdesc = {description.replace(chr(10), ' ').strip()}",
            f"builddate = {int(datetime.now(timezone.utc).timestamp())}",
            f"packager = {AUTHOR}", f"url = {REPO}", f"size = {payload_size}",
            f"arch = {arch}",
        ]
        license_value = control.get("License", "")
        for license_name in license_value.split():
            metadata.append(f"license = {license_name}")
        for dependency in parse_dep_list(control.get("Depends", "")):
            mapped = map_dep(dependency, cfg)
            if mapped:
                metadata.append(f"depend = {mapped}")
        (safe_root / ".PKGINFO").write_text("\n".join(metadata) + "\n", encoding="utf-8")
        temp_output = output.parent / f".{output.name}.tmp-{os.getpid()}"
        try:
            _run_checked([archiver, "-c", "--zstd", "-f", str(temp_output), "-C", str(safe_root), "."],
                         "Could not create the Arch package archive")
            os.replace(temp_output, output)
        except Exception:
            temp_output.unlink(missing_ok=True)
            raise
    ok(f"Created {output}.")

def _deb_to_arch_arch(value: str) -> str:
    return {"amd64": "x86_64", "arm64": "aarch64", "armhf": "armv7h",
            "i386": "i686", "all": "any"}.get(value.lower(), value.lower())

def cmd_export(args: argparse.Namespace, cfg: Config) -> None:
    database = load_db(cfg)
    selected = database.items() if not args.package else ((args.package, database[args.package]),) if args.package in database else ()
    packages = []
    for name, entry in selected:
        packages.append({"name": name, "version": entry.get("version", "0"),
                         "source_url": entry.get("source_url", ""),
                         "source_deb": entry.get("source_deb", ""),
                         "sha256": entry.get("sha256", ""),
                         "debian_deps": entry.get("debian_deps", []),
                         "arch_deps": entry.get("arch_deps", [])})
    if args.package and not packages:
        fail(f"Not installed via DebArk: {args.package}")
    payload = {
        "schema_version": 1,
        "exported_at": utc_now(),
        "generated_by": {
            "name": "DebArk",
            "version": VERSION,
            "maintainer": AUTHOR,
            "repository": REPO,
        },
        "packages": packages,
    }
    destination = Path(args.output).expanduser().absolute()
    write_json(destination, payload)
    ok(f"Exported {len(packages)} package record(s) to {destination}.")

def cmd_import(args: argparse.Namespace, cfg: Config) -> None:
    path = Path(args.file).expanduser().resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DebArkError(f"Could not read package export: {exc}") from exc
    packages = payload.get("packages") if isinstance(payload, dict) else None
    if not isinstance(packages, list):
        fail("Import file must contain a packages array.")
    failures = 0
    for item in packages:
        if not isinstance(item, dict) or not PACKAGE_NAME.fullmatch(str(item.get("name", ""))):
            warn("Skipping an invalid package record.")
            failures += 1
            continue
        source = str(item.get("source_url") or item.get("source_deb") or "")
        if not source:
            warn(f"Skipping {item['name']}: no package source was recorded.")
            failures += 1
            continue
        install_args = argparse.Namespace(
            deb=source, yes=args.yes, no_deps=args.no_deps, dry_run=args.dry_run,
            user=getattr(args, "user", False), no_color=getattr(args, "no_color", False),
            threads=getattr(args, "threads", None), json=False, quiet=getattr(args, "quiet", False),
            verbose=getattr(args, "verbose", False), snapshot=args.snapshot, sandbox=args.sandbox,
            verify_after=True, profile=None, expected_sha256=item.get("sha256", ""),
        )
        try:
            cmd_install(install_args, cfg)
        except DebArkError as exc:
            warn(f"Could not import {item['name']}: {exc}")
            failures += 1
    if failures:
        fail(f"Import finished with {failures} skipped or failed package(s).")

def cmd_bulk(args: argparse.Namespace, cfg: Config) -> None:
    path = Path(args.file).expanduser().resolve()
    try:
        entries = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.lstrip().startswith("#")]
    except OSError as exc:
        raise DebArkError(f"Could not read bulk list: {exc}") from exc
    failures = 0
    for item in entries:
        try:
            if args.action == "install":
                cmd_install(argparse.Namespace(
                    deb=item, yes=args.yes, no_deps=args.no_deps, dry_run=args.dry_run,
                    snapshot=args.snapshot, sandbox=args.sandbox, verify_after=True,
                    user=getattr(args, "user", False), no_color=getattr(args, "no_color", False),
                    threads=getattr(args, "threads", None), json=False, quiet=getattr(args, "quiet", False),
                    verbose=getattr(args, "verbose", False), profile=None), cfg)
            else:
                cmd_remove(argparse.Namespace(package=item, yes=args.yes), cfg)
        except DebArkError as exc:
            warn(f"Bulk operation failed for {item}: {exc}")
            failures += 1
    if failures:
        fail(f"Bulk operation finished with {failures} failure(s).")

def cmd_watch(args: argparse.Namespace, cfg: Config) -> None:
    directory = Path(args.directory).expanduser().resolve()
    if not directory.is_dir():
        fail(f"Watch path is not a directory: {directory}")
    if not 0.25 <= args.interval <= 3600:
        fail("Watch interval must be between 0.25 and 3600 seconds.")
    seen: set[str] = set()
    observed: dict[str, tuple[int, int, int]] = {}
    info(f"Watching {directory} for Debian packages. Press Ctrl+C to stop.")
    try:
        while True:
            for path in sorted(directory.glob("*.deb")):
                key = str(path.resolve())
                if key in seen or not path.is_file():
                    continue
                try:
                    metadata = path.stat()
                except OSError:
                    continue
                previous = observed.get(key)
                if previous and previous[:2] == (metadata.st_size, metadata.st_mtime_ns):
                    stable_count = previous[2] + 1
                else:
                    stable_count = 1
                observed[key] = (metadata.st_size, metadata.st_mtime_ns, stable_count)
                if stable_count < 2:
                    continue
                seen.add(key)
                print(f"New package: {path.name}")
                if not (args.yes or cfg.auto_yes or _ask_yes_no(f"Install {path.name}?", False)):
                    continue
                try:
                    cmd_install(argparse.Namespace(
                        deb=str(path), yes=args.yes, no_deps=args.no_deps, dry_run=False,
                        snapshot=args.snapshot, sandbox=args.sandbox, verify_after=True,
                        user=getattr(args, "user", False), no_color=getattr(args, "no_color", False),
                        threads=getattr(args, "threads", None), json=False, quiet=getattr(args, "quiet", False),
                        verbose=getattr(args, "verbose", False), profile=None), cfg)
                except DebArkError as exc:
                    warn(str(exc))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped watching.")

def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    try:
        answer = input(f"{UI.color('›', '36')} {prompt} [{suffix}] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not answer:
        return default
    return answer in ("y", "yes")

def run_tui(profile_name: str | None = None, no_color: bool = False,
            user_mode: bool = False) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("debark: interactive mode requires a terminal.", file=sys.stderr)
        return 2
    UI.setup(not no_color)
    profile: dict[str, Any] = {}
    if profile_name:
        if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", profile_name):
            print("debark: invalid profile name.", file=sys.stderr)
            return 2
        profile_paths = (current_user_home() / ".config/debark/profiles" / f"{profile_name}.json",
                         Path("/etc/debark/profiles") / f"{profile_name}.json")
        profile_path = next((item for item in profile_paths if item.is_file() and not item.is_symlink()), None)
        try:
            if profile_path is None:
                raise FileNotFoundError(profile_name)
            value = json.loads(read_regular_text(profile_path))
            if isinstance(value, dict):
                profile = value
            else:
                raise ValueError("profile must be a JSON object")
        except (OSError, ValueError):
            print(f"debark: profile not found: {profile_name}", file=sys.stderr)
            return 2
    header("DebArk · Interactive installer")
    print(f"{UI.color('Maintainer:', '2')} {AUTHOR}")
    print(f"{UI.color('Project:', '2')} {REPO}\n")
    try:
        mode_default = "2" if (
            user_mode or profile.get("mode") == "user" or os.geteuid() != 0
        ) else "1"
        while True:
            prompt = "Install mode: 1) system  2) user"
            mode = input(f"{UI.color(prompt, '36')} [{mode_default}]: ").strip() or mode_default
            if mode in ("1", "2"):
                break
            print("Choose 1 or 2.")
        force_user = mode == "2"
        if force_user and os.geteuid() == 0 and os.environ.get("SUDO_USER"):
            print("Interactive user mode must be run without sudo.", file=sys.stderr)
            return 2
        if not force_user and os.geteuid() != 0:
            try:
                return request_system_access()
            except DebArkError as exc:
                print(f"debark: error: {exc}", file=sys.stderr)
                return 1
        deb = input(f"{UI.color('Package path or URL:', '36')} ").strip()
        if not deb:
            print("No package selected.")
            return 1
        yes = _ask_yes_no("Auto-confirm installation and dependency prompts?", bool(profile.get("auto_yes", False)))
        no_deps = _ask_yes_no("Skip Arch dependency installation?", False)
        snapshot = _ask_yes_no("Save a local restore point after install?", bool(profile.get("snapshot", False)))
        sandbox = _ask_yes_no("Launch through an available sandbox runtime?", bool(profile.get("sandbox", False)))
        verify = _ask_yes_no("Verify payload hashes after install?", bool(profile.get("verify", True)))
        try:
            thread_default = str(max(1, min(16, int(profile.get("threads", 4)))))
        except (TypeError, ValueError):
            thread_default = "4"
        raw_threads = input(f"Parallel workers (1-16) [{thread_default}]: ").strip() or thread_default
        try:
            threads = max(1, min(16, int(raw_threads)))
        except ValueError:
            print("Invalid worker count; using 4.")
            threads = 4
        save_profile = _ask_yes_no("Save these choices as a profile?", False)
        saved_profile_name = ""
        if save_profile:
            saved_profile_name = input("Profile name: ").strip()
            if not saved_profile_name:
                print("No profile name entered; continuing without saving.")
            elif not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", saved_profile_name):
                print("Invalid profile name; continuing without saving.")
                saved_profile_name = ""
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 130

    cfg = Config(force_user=force_user)
    cfg.threads = threads
    if saved_profile_name:
        cfg.ensure()
        profile_path = cfg.profiles_dir / f"{saved_profile_name}.json"
        write_json(profile_path, {
            "managed_by": "DebArk", "maintainer": AUTHOR, "repository": REPO,
            "mode": cfg.mode, "auto_yes": yes, "snapshot": snapshot,
            "sandbox": sandbox, "verify": verify, "threads": threads,
        })
        if cfg.mode == "user":
            set_user_owner(profile_path)
    UI.setup(cfg.colors and not no_color)
    args = argparse.Namespace(
        deb=deb, yes=yes, no_deps=no_deps, dry_run=False,
        user=force_user, no_color=False, threads=threads, snapshot=snapshot,
        sandbox=sandbox, verify_after=verify, expected_sha256="", profile=profile_name,
    )
    try:
        cmd_install(args, cfg)
    except DebArkError as exc:
        print(f"debark: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ndebark: interrupted", file=sys.stderr)
        return 130
    return 0

HELP_TEXT = f"""DebArk {VERSION} — install Debian packages on Arch Linux
Maintainer: {AUTHOR}
Project: {REPO}
License: {__license__}

Usage: debark [GLOBAL OPTIONS] COMMAND [OPTIONS]

Commands:
  install (-S, -i) PACKAGE.deb|URL  Inspect and install a Debian package
  remove (-R, -r) PACKAGE           Remove a DebArk-managed package
  list (-Q, -l)                     List installed packages
  search (-Qs) QUERY                Search installed packages
  info (-Qi, -s) PACKAGE.deb|NAME   Inspect a .deb or installed package
  files (-Ql, -L) PACKAGE           List files managed for a package
  verify (-Qk, -V) PACKAGE          Check managed file hashes
  scan                      Find xattr markers under the app directory
  repair PACKAGE            Restore missing or damaged managed files
  gc                        Clean orphaned DebArk markers
  update (-Fy)              Refresh Arch file metadata and local maps
  upgrade (-Qu)             Check for newer versions in synced beta APT indexes
  snapshot PACKAGE          Save a verified local restore point
  rollback PACKAGE [ID]     Restore a saved local restore point
  extract PACKAGE.deb       Extract payload without installing
  convert PACKAGE.deb       Create an Arch .pkg.tar.zst package
  export [PACKAGE]           Export package source records
  import FILE                Install sources from an export
  bulk install|remove FILE   Run a text-file batch
  watch DIRECTORY            Monitor for new .deb files
  profile list|save|remove   Manage TUI preference profiles
  plugin list|enable|disable Manage trusted local hook plugins
  pin [PACKAGE VERSION]      Manage package version pins
  license [PACKAGE]          Show declared package licenses
  config [KEY VALUE]        Show or change configuration
  doctor                    Inspect local prerequisites
  repo add|remove|list|sync|search|info|install  Manage Debian sources (beta search/info/install)
  -Ss QUERY                 Search packages in synced beta APT indexes
  -Si PACKAGE               Show package details from synced beta APT indexes
  cve --suite SUITE         Check installed packages against Debian advisories (beta)
  log                       Show recent audit events
  stats                     Show installed package statistics
  help [install|repo]       Show help
  about                     Show version and project information

Global options:
  -u, --user                Use the current user's installation paths
  -tui, --tui               Start the interactive installer
  --threads N               Parallel copy workers, from 1 through 16
  --no-color                Disable colored output
  --quiet                   Suppress informational output
  --verbose                 Show additional diagnostic information
  --json                    Use machine-readable output where supported
  --profile NAME             Load an interactive TUI profile

Install options:
  -y, --yes                 Skip confirmation prompts
  --dry-run                 Preview without installing
  --no-deps                 Do not install mapped Arch dependencies
  --snapshot                Save a local restore point after install
  --sandbox                 Generate launchers using Firejail or bubblewrap
  --sha256 HASH             Require a specific package SHA256
  --gpg-signature FILE      Verify a detached GPG signature
  --keyring FILE            Set the trusted verification keyring

Package payload is kept under /opt/PACKAGE or ~/.local/opt/PACKAGE.
DebArk does not run Debian maintainer scripts.
System commands from a trusted installation request access through sudo or doas.
Shortcuts match common pacman/dpkg habits; they run one DebArk action and do not
perform Arch system upgrades such as pacman -Syu.
"""

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="debark",
        add_help=True,
        description=(
            "Install Debian packages on Arch Linux.\n"
            f"Maintainer: {AUTHOR}\n"
            f"Project: {REPO}\n"
            f"License: {__license__}"
        ),
        epilog=(
            "Common command shortcuts:\n"
            "  -S/-i install   -R/-r remove   -Q/-l list   -Qs search installed\n"
            "  -Qi/-s info     -Ql/-L files   -Qk/-V verify\n"
            "  -Ss repo search -Si repo info  -Fy update   -Qu upgrade check\n"
            "These run one DebArk action; combined pacman system-upgrade forms are not supported."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version",
        version=f"DebArk {VERSION}\nMaintainer: {AUTHOR}\nProject: {REPO}",
    )
    parser.add_argument("--user", "-u", action="store_true", help="use per-user paths")
    parser.add_argument("--no-color", action="store_true", help="disable colors")
    parser.add_argument("--threads", type=int, help="parallel copy workers")
    parser.add_argument("--profile", help="load interactive preferences")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--quiet", "-q", action="store_true", help="suppress informational output")
    parser.add_argument("--verbose", "-v", action="store_true", help="show diagnostic details")
    parser.add_argument("-tui", "--tui", action="store_true", help="start interactive mode")
    sub = parser.add_subparsers(dest="command")
    def common(child: argparse.ArgumentParser) -> None:
        child.add_argument("--user", "-u", action="store_true", default=argparse.SUPPRESS)
        child.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS)
        child.add_argument("--threads", type=int, default=argparse.SUPPRESS)
        child.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        child.add_argument("--quiet", "-q", action="store_true", default=argparse.SUPPRESS)
        child.add_argument("--verbose", "-v", action="store_true", default=argparse.SUPPRESS)
    install = sub.add_parser("install", help="install a Debian package")
    common(install)
    install.add_argument("deb")
    install.add_argument("-y", "--yes", action="store_true")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument("--no-deps", action="store_true")
    snapshot_group = install.add_mutually_exclusive_group()
    snapshot_group.add_argument("--snapshot", dest="snapshot", action="store_true")
    snapshot_group.add_argument("--no-snapshot", dest="snapshot", action="store_false")
    install.set_defaults(snapshot=False)
    install.add_argument("--sandbox", action="store_true")
    install.add_argument("--version", dest="requested_version", help="require this Debian package version")
    install.add_argument("--sha256", help="require this package SHA256")
    install.add_argument("--gpg-signature", help="verify this detached signature")
    install.add_argument("--keyring", help="trusted GPG keyring used to verify the signature")
    verify_group = install.add_mutually_exclusive_group()
    verify_group.add_argument("--verify", dest="verify_after", action="store_true")
    verify_group.add_argument("--no-verify", dest="verify_after", action="store_false")
    install.set_defaults(verify_after=True)
    remove = sub.add_parser("remove", help="remove a managed package")
    common(remove)
    remove.add_argument("package")
    remove.add_argument("-y", "--yes", action="store_true")
    for name, help_text in (("list", "list managed packages"), ("scan", "scan for markers"),
                            ("update", "refresh local metadata"), ("upgrade", "show upgrade status"),
                            ("doctor", "check prerequisites"), ("about", "show project details")):
        child = sub.add_parser(name, help=help_text)
        common(child)
    for name, argument in (("info", "deb"), ("search", "query"), ("files", "package"),
                            ("verify", "package")):
        child = sub.add_parser(name)
        common(child)
        child.add_argument(argument)
    config = sub.add_parser("config")
    common(config)
    config.add_argument("key", nargs="?")
    config.add_argument("value", nargs="?")
    repo = sub.add_parser("repo")
    common(repo)
    repo_sub = repo.add_subparsers(dest="repo_command")
    add = repo_sub.add_parser("add")
    common(add)
    add.add_argument("name")
    add.add_argument("url")
    add.add_argument("suite")
    add.add_argument("components", nargs="+")
    add.add_argument("--keyring", help="trusted keyring for experimental APT sync")
    remove_repo = repo_sub.add_parser("remove")
    common(remove_repo)
    remove_repo.add_argument("name")
    repo_list = repo_sub.add_parser("list")
    common(repo_list)
    repo_search = repo_sub.add_parser("search")
    common(repo_search)
    repo_search.add_argument("query")
    repo_search.add_argument("--repo", help="search one registered repository")
    repo_info = repo_sub.add_parser("info")
    common(repo_info)
    repo_info.add_argument("package")
    repo_info.add_argument("--repo", help="inspect one registered repository")
    repo_sync = repo_sub.add_parser("sync")
    common(repo_sync)
    repo_sync.add_argument("name", nargs="?")
    repo_install = repo_sub.add_parser("install")
    common(repo_install)
    repo_install.add_argument("name")
    repo_install.add_argument("package")
    repo_install.add_argument("version", nargs="?")
    repo_install.add_argument("-y", "--yes", action="store_true")
    repo_install.add_argument("--no-deps", action="store_true")
    repo_install.add_argument("--sandbox", action="store_true",
                              help="launch the app with the optional network-isolated sandbox")
    cve = sub.add_parser("cve", help="check advisories (experimental)")
    common(cve)
    cve.add_argument("--suite", required=True, help="Debian suite, such as bookworm")
    help_parser = sub.add_parser("help")
    help_parser.add_argument("topic", nargs="?")
    for name in ("repair", "gc", "log", "stats"):
        child = sub.add_parser(name)
        common(child)
        if name == "repair":
            child.add_argument("package")
            child.add_argument("-y", "--yes", action="store_true")
        elif name == "gc":
            child.add_argument("-y", "--yes", action="store_true")
    snapshot = sub.add_parser("snapshot", help="save a local restore point")
    common(snapshot)
    snapshot.add_argument("package")
    rollback = sub.add_parser("rollback", help="restore a saved package tree")
    common(rollback)
    rollback.add_argument("package")
    rollback.add_argument("snapshot", nargs="?")
    rollback.add_argument("-y", "--yes", action="store_true")
    extract = sub.add_parser("extract", help="extract package payload without installing")
    common(extract)
    extract.add_argument("deb")
    extract.add_argument("-o", "--output")
    convert = sub.add_parser("convert", help="convert a .deb to an Arch package")
    common(convert)
    convert.add_argument("deb")
    convert.add_argument("-o", "--output")
    export = sub.add_parser("export", help="export package source records")
    common(export)
    export.add_argument("package", nargs="?")
    export.add_argument("-o", "--output", default="debark-export.json")
    import_cmd = sub.add_parser("import", help="install package sources from an export")
    common(import_cmd)
    import_cmd.add_argument("file")
    import_cmd.add_argument("-y", "--yes", action="store_true")
    import_cmd.add_argument("--no-deps", action="store_true")
    import_cmd.add_argument("--dry-run", action="store_true")
    import_cmd.add_argument("--snapshot", action="store_true")
    import_cmd.add_argument("--sandbox", action="store_true")
    bulk = sub.add_parser("bulk", help="install or remove packages from a text file")
    common(bulk)
    bulk.add_argument("action", choices=("install", "remove"))
    bulk.add_argument("file")
    bulk.add_argument("-y", "--yes", action="store_true")
    bulk.add_argument("--no-deps", action="store_true")
    bulk.add_argument("--dry-run", action="store_true")
    bulk.add_argument("--snapshot", action="store_true")
    bulk.add_argument("--sandbox", action="store_true")
    watch = sub.add_parser("watch", help="watch a directory for new .deb files")
    common(watch)
    watch.add_argument("directory")
    watch.add_argument("--interval", type=float, default=2.0)
    watch.add_argument("-y", "--yes", action="store_true")
    watch.add_argument("--no-deps", action="store_true")
    watch.add_argument("--snapshot", action="store_true")
    watch.add_argument("--sandbox", action="store_true")
    profile_cmd = sub.add_parser("profile", help="manage interactive preference profiles")
    common(profile_cmd)
    profile_sub = profile_cmd.add_subparsers(dest="profile_command", required=True)
    profile_save = profile_sub.add_parser("save")
    common(profile_save)
    profile_save.add_argument("name")
    profile_remove = profile_sub.add_parser("remove")
    common(profile_remove)
    profile_remove.add_argument("name")
    profile_list = profile_sub.add_parser("list")
    common(profile_list)
    plugin_cmd = sub.add_parser("plugin", help="manage trusted local hook plugins")
    common(plugin_cmd)
    plugin_sub = plugin_cmd.add_subparsers(dest="plugin_command", required=True)
    plugin_list = plugin_sub.add_parser("list")
    common(plugin_list)
    for action in ("enable", "disable"):
        child = plugin_sub.add_parser(action)
        common(child)
        child.add_argument("name")
    pin = sub.add_parser("pin", help="pin an installed package version")
    common(pin)
    pin.add_argument("package", nargs="?")
    pin.add_argument("version", nargs="?")
    pin.add_argument("--clear", action="store_true")
    license_cmd = sub.add_parser("license", help="show recorded license declarations")
    common(license_cmd)
    license_cmd.add_argument("package", nargs="?")
    return parser

def _quiet_summary(command: str, output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    markers = ("Installed ", "Removed ", "Restored ", "Saved ", "Created ",
               "Extracted ", "Exported ", "Set ", "Cleared ", "No packages", "No matches")
    for line in reversed(lines):
        if line.startswith(markers) or line.startswith("[✓]"):
            return line
    return lines[-1]

def main() -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_command_shortcuts(sys.argv[1:]))
    if getattr(args, "tui", False):
        UI.setup(not getattr(args, "no_color", False))
        if getattr(args, "json", False):
            print("debark: JSON output is not available in interactive mode.", file=sys.stderr)
            return 2
        return run_tui(
            getattr(args, "profile", None),
            no_color=getattr(args, "no_color", False),
            user_mode=getattr(args, "user", False),
        )
    if args.command is None:
        print(HELP_TEXT)
        return 0
    if args.command == "help":
        if args.topic not in (None, "install", "repo"):
            parser.error(f"unknown help topic: {args.topic}")
        print(HELP_TEXT)
        return 0
    cfg = Config(force_user=(getattr(args, "user", False) or _running_from_user_install()))
    if getattr(args, "threads", None) is not None:
        cfg.threads = max(1, min(16, args.threads))
    UI.setup(cfg.colors and not getattr(args, "no_color", False))
    global _OUTPUT_QUIET, _OUTPUT_JSON
    _OUTPUT_QUIET = bool(getattr(args, "quiet", False))
    _OUTPUT_JSON = bool(getattr(args, "json", False))
    handlers = {
        "install": cmd_install, "remove": cmd_remove, "list": cmd_list,
        "info": cmd_info, "search": cmd_search, "files": cmd_files,
        "verify": cmd_verify, "scan": cmd_scan, "update": cmd_update,
        "upgrade": cmd_upgrade, "repo": cmd_repo, "cve": cmd_cve, "config": cmd_config,
        "doctor": cmd_doctor, "about": cmd_about,
        "repair": cmd_repair, "gc": cmd_gc, "log": cmd_log, "stats": cmd_stats,
        "snapshot": cmd_snapshot, "rollback": cmd_rollback, "extract": cmd_extract,
        "convert": cmd_convert, "export": cmd_export, "import": cmd_import,
        "bulk": cmd_bulk, "watch": cmd_watch, "profile": cmd_profile,
        "plugin": cmd_plugin, "pin": cmd_pin,
        "license": cmd_license,
    }
    try:
        if cfg.mode == "user" and os.geteuid() == 0 and os.environ.get("SUDO_USER"):
            fail("Run user-mode commands without sudo; use sudo only for system mode.")
        needs_system_access = args.command in SYSTEM_ACCESS_COMMANDS
        if args.command == "info":
            needs_system_access = not Path(args.deb).expanduser().is_file()
        if (cfg.mode == "system" and os.geteuid() != 0
                and needs_system_access
                and os.environ.get("DEBARK_COMPLETION") != "1"):
            return request_system_access()
        cfg.validate()
        if _OUTPUT_JSON and args.command == "watch":
            fail("JSON output is not supported for the continuous watch command.")
        handler = handlers[args.command]
        if _OUTPUT_JSON and args.command not in ("stats", "log", "list"):
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                handler(args, cfg)
            lines = captured.getvalue().splitlines()
            cancelled = any(line.strip() in ("Aborted.", "Cancelled.") for line in lines)
            print(json.dumps({"success": not cancelled, "command": args.command,
                              "output": lines}, ensure_ascii=False))
        elif _OUTPUT_QUIET and args.command != "watch":
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                handler(args, cfg)
            summary = _quiet_summary(args.command, captured.getvalue())
            if summary:
                print(summary)
        else:
            handler(args, cfg)
    except DebArkError as exc:
        if _OUTPUT_JSON and args.command not in ("stats", "log", "list"):
            print(json.dumps({"success": False, "command": args.command,
                              "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"debark: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if _OUTPUT_JSON and args.command not in ("stats", "log", "list"):
            print(json.dumps({"success": False, "command": args.command,
                              "error": "interrupted"}))
        else:
            print("\ndebark: interrupted", file=sys.stderr)
        return 130
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
