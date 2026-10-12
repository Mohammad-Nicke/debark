"""Translate a safe subset of APT commands to DebArk operations."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Invocation:
    action: str
    operands: tuple[str, ...]
    assume_yes: bool = False
    quiet: bool = False
    only_upgrade: bool = False
    reinstall: bool = False
    installed_only: bool = False
    purge: bool = False
    fix_broken: bool = False


_ACTIONS = {
    "apt": {"update", "install", "reinstall", "remove", "purge", "upgrade", "list"},
    "apt-get": {"update", "install", "reinstall", "remove", "purge", "upgrade"},
    "apt-cache": {"search", "show", "policy"},
    "dpkg": {"install local .deb", "list", "status", "listfiles", "architecture"},
}
_SAFE_OPTIONS = {
    "-y": "assume_yes",
    "--yes": "assume_yes",
    "--assume-yes": "assume_yes",
    "-q": "quiet",
    "-qq": "quiet",
    "--quiet": "quiet",
    "--no-install-recommends": "no_install_recommends",
    "--only-upgrade": "only_upgrade",
    "--reinstall": "reinstall",
    "--installed": "installed_only",
    "--purge": "purge",
    "-f": "fix_broken",
    "--fix-broken": "fix_broken",
}
_DPKG_ACTIONS = {
    "-i": "install-local", "--install": "install-local",
    "-l": "list", "--list": "list",
    "-s": "status", "--status": "status",
    "-L": "listfiles", "--listfiles": "listfiles",
    "--print-architecture": "architecture",
}


def _parse_dpkg(tokens: list[str], engine: Any) -> Invocation:
    if not tokens:
        engine.fail(
            "Use dpkg --list, --status PACKAGE, --listfiles PACKAGE, "
            "or --print-architecture."
        )
    action = _DPKG_ACTIONS.get(tokens[0])
    if action is None:
        engine.fail(
            "DebArk dpkg compatibility supports local .deb installation and read-only "
            "queries; other mutations and maintainer scripts are not supported."
        )
    operands = tokens[1:]
    if action == "install-local" and operands[:1] == ["--"]:
        operands = operands[1:]
    if action == "install-local":
        if not operands:
            engine.fail("dpkg -i requires at least one local .deb file.")
        if any(item.startswith("-") for item in operands):
            engine.fail("dpkg -i accepts local .deb paths only; unsupported options were supplied.")
        return Invocation(action=action, operands=tuple(operands))
    if any(item.startswith("-") for item in operands):
        engine.fail("dpkg compatibility accepts package names only after a query operation.")
    if action == "architecture" and operands:
        engine.fail("dpkg --print-architecture does not accept package names.")
    if action not in {"architecture", "list"} and not operands:
        engine.fail(f"dpkg {tokens[0]} requires at least one package name.")
    return Invocation(action=action, operands=tuple(operands))


def _parse(tool: str, tokens: list[str], engine: Any) -> Invocation:
    if tool not in _ACTIONS:
        engine.fail(f"Unsupported APT compatibility tool: {tool}.")
    if tool == "dpkg":
        return _parse_dpkg(tokens, engine)

    action = ""
    operands: list[str] = []
    flags: set[str] = set()
    options_ended = False
    for token in tokens:
        if token == "--" and not options_ended:
            options_ended = True
            continue
        option = _SAFE_OPTIONS.get(token) if not options_ended else None
        if option:
            flags.add(option)
        elif token.startswith("-") and not options_ended:
            engine.fail(f"APT compatibility does not support option {token!r}.")
        elif not action:
            action = token
        else:
            operands.append(token)

    if action not in _ACTIONS[tool]:
        supported = ", ".join(sorted(_ACTIONS[tool]))
        engine.fail(
            f"Unsupported {tool} operation {action or '(none)'!r}. "
            f"Supported operations: {supported}."
        )

    if "only_upgrade" in flags and action != "install":
        engine.fail("--only-upgrade is supported only with apt install or apt-get install.")
    if "reinstall" in flags and action != "install":
        engine.fail("--reinstall is supported only with apt-get install.")
    if "no_install_recommends" in flags and action != "install":
        engine.fail(
            "--no-install-recommends is supported only with apt install "
            "or apt-get install."
        )
    if "installed_only" in flags and (tool != "apt" or action != "list"):
        engine.fail("--installed is supported only with apt list.")
    if "purge" in flags and action not in {"remove", "purge"}:
        engine.fail("--purge is supported only with apt remove or purge.")
    if "fix_broken" in flags and (tool not in {"apt", "apt-get"} or action != "install"):
        engine.fail("--fix-broken is supported only with apt install or apt-get install.")
    apt_cache_only_flags = {
        "assume_yes", "only_upgrade", "reinstall", "installed_only", "purge",
        "no_install_recommends",
    }
    if tool == "apt-cache" and flags.intersection(apt_cache_only_flags):
        engine.fail("That option does not apply to apt-cache.")

    return Invocation(
        action=action,
        operands=tuple(operands),
        assume_yes="assume_yes" in flags,
        quiet="quiet" in flags,
        only_upgrade="only_upgrade" in flags,
        reinstall="reinstall" in flags or action == "reinstall",
        installed_only="installed_only" in flags,
        purge="purge" in flags or action == "purge",
        fix_broken="fix_broken" in flags,
    )


def _repository_candidates(
    package: str, version: str | None, indexes: dict[str, dict[str, Any]], engine: Any,
) -> tuple[str, dict[str, str]] | None:
    candidates: list[tuple[str, dict[str, str]]] = []
    for name, index in indexes.items():
        candidate = engine.experimental.newest_package(index["packages"], package, version)
        if candidate:
            candidates.append((name, candidate))
    if not candidates:
        return None

    selected_name, selected = candidates[0]
    for name, candidate in candidates[1:]:
        comparison = engine.experimental.compare_debian_versions(
            candidate["Version"], selected["Version"]
        )
        if comparison > 0 or (comparison == 0 and name < selected_name):
            selected_name, selected = name, candidate
    return selected_name, selected


def _package_spec(spec: str, engine: Any) -> tuple[str, str | None]:
    package_part, separator, version = spec.partition("=")
    if separator and (
        not version or not all(char.isalnum() or char in ".:+~_-" for char in version)
    ):
        engine.fail(f"Invalid Debian version in package specification: {spec!r}.")
    package, architecture_separator, architecture = package_part.partition(":")
    if architecture_separator and architecture not in {"amd64", "all"}:
        engine.fail(
            f"APT compatibility on this system supports only amd64 packages, "
            f"not {architecture!r}."
        )
    if not engine.PACKAGE_NAME.fullmatch(package):
        engine.fail(f"Invalid Debian package specification: {spec!r}.")
    return package, version if separator else None


def _require_arguments(invocation: Invocation, engine: Any) -> None:
    if not invocation.operands:
        engine.fail(f"{invocation.action} requires at least one package or search term.")


def _ordered_install_plan(
    engine: Any, cfg: Any, plans: list[tuple[str, str, dict[str, str]]],
    database: dict[str, Any],
) -> list[tuple[str, str, dict[str, str]]]:
    plan_map = {package: (repo_name, candidate) for package, repo_name, candidate in plans}
    ordered: list[tuple[str, str, dict[str, str]]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def is_installed_arch_package(name: str) -> bool:
        try:
            return subprocess.run(
                ["pacman", "-Qq", name], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=False,
            ).returncode == 0
        except OSError as exc:
            engine.fail(f"Could not check Arch dependency {name}: {exc}.")
            return False

    def visit(package: str) -> None:
        if package in visited:
            return
        if package in visiting:
            engine.fail(f"The requested APT packages contain a dependency cycle at {package}.")
        visiting.add(package)
        repo_name, candidate = plan_map[package]
        for dependency in engine.parse_dep_list(candidate.get("Depends", "")):
            if dependency in plan_map:
                visit(dependency)
                continue
            if dependency in database:
                continue
            mapped = engine.map_dep(dependency, cfg)
            if mapped is None:
                if is_installed_arch_package(dependency):
                    continue
                engine.fail(
                    f"APT dependency {dependency} cannot be safely mapped to an Arch package. "
                    "Install that dependency separately or use a package with "
                    "supported dependencies."
                )
            installed = is_installed_arch_package(mapped)
            if cfg.mode == "user" and not installed:
                engine.fail(
                    f"APT dependency {dependency} maps to Arch package {mapped}, "
                    "which is not installed. "
                    "Install system dependencies with pacman before using the user-mode wrapper."
                )
        visiting.remove(package)
        visited.add(package)
        ordered.append((package, repo_name, candidate))

    for package, _repo_name, _candidate in plans:
        visit(package)
    return ordered


def _install(engine: Any, cfg: Any, invocation: Invocation) -> None:
    if invocation.fix_broken and not invocation.operands:
        engine.cmd_fix(engine.argparse.Namespace(adopt=False, yes=True), cfg)
        return
    engine._require_experimental(cfg)
    _require_arguments(invocation, engine)
    repositories = engine.load_repos(cfg)
    indexes = engine._load_repo_indexes(cfg, repositories)
    database = engine.load_db(cfg)
    pins = engine.load_pins(cfg)
    plans: list[tuple[str, str, dict[str, str]]] = []
    seen: set[str] = set()

    for spec in invocation.operands:
        package, requested_version = _package_spec(spec, engine)
        if package in seen:
            continue
        seen.add(package)
        installed = database.get(package)
        if invocation.only_upgrade and not installed:
            engine.fail(
                f"{package} is not installed through DebArk and cannot be upgraded "
                "with --only-upgrade."
            )
        selected = _repository_candidates(
            package, requested_version, indexes, engine
        )
        if selected is None:
            engine.fail(f"{package} was not found in the currently synced, verified APT indexes.")
        repo_name, candidate = selected
        version = candidate["Version"]
        if package in pins and pins[package] != version:
            engine.fail(
                f"{package} is pinned to {pins[package]}, but the selected APT "
                f"version is {version}."
            )
        current_version = str(installed.get("version", "0")) if installed else ""
        if installed and current_version:
            comparison = engine.experimental.compare_debian_versions(version, current_version)
            if comparison < 0:
                engine.fail(f"Refusing to downgrade {package} from {current_version} to {version}.")
            if comparison == 0 and not invocation.reinstall:
                engine.info(f"{package} {current_version} is already installed.")
                continue
        plans.append((package, repo_name, candidate))

    if not plans:
        return
    plans = _ordered_install_plan(engine, cfg, plans, database)

    print("DebArk will install or update these applications:")
    for package, repo_name, candidate in plans:
        current = database.get(package)
        old_version = str(current.get("version", "")) if current else ""
        change = f"{old_version} → " if old_version else ""
        print(f"  {package}: {change}{candidate['Version']} ({repo_name})")
    if len(plans) > 1:
        print(
            "Each application is committed separately; an error may leave earlier "
            "successful items installed."
        )
    if not engine.confirm(
        "Continue with these DebArk package changes?", invocation.assume_yes or cfg.auto_yes
    ):
        engine.warn("Cancelled.")
        return

    for package, repo_name, candidate in plans:
        engine.cmd_repo(
            engine.argparse.Namespace(
                repo_command="install", name=repo_name, package=package,
                version=candidate["Version"], yes=True, no_deps=False, sandbox=False,
            ),
            cfg,
        )


def _remove(engine: Any, cfg: Any, invocation: Invocation) -> None:
    _require_arguments(invocation, engine)
    database = engine.load_db(cfg)
    packages: list[str] = []
    for spec in invocation.operands:
        package, version = _package_spec(spec, engine)
        if version is not None:
            engine.fail("Version-qualified removal is not supported; remove a package by name.")
        if package not in database:
            engine.fail(f"Not installed through DebArk: {package}.")
        if package not in packages:
            packages.append(package)

    print("DebArk will remove these managed applications:")
    for package in packages:
        print(f"  {package} {database[package].get('version', '?')}")
    if invocation.purge:
        print(
            "DebArk has no Debian maintainer scripts or separate APT configuration "
            "database to purge."
        )
    if len(packages) > 1:
        print(
            "Each application is removed separately; an error may leave earlier "
            "successful items removed."
        )
    if not engine.confirm(
        "Continue with these DebArk removals?", invocation.assume_yes or cfg.auto_yes
    ):
        engine.warn("Cancelled.")
        return

    for package in packages:
        engine.cmd_remove(engine.argparse.Namespace(package=package, yes=True), cfg)


def _install_local_debs(engine: Any, cfg: Any, invocation: Invocation) -> None:
    sources: list[Path] = []
    for raw_path in invocation.operands:
        source = Path(raw_path).expanduser()
        if source.suffix.lower() != ".deb":
            engine.fail(f"dpkg -i accepts .deb files only: {raw_path!r}.")
        if source.is_symlink() or not source.is_file():
            engine.fail(f"Local Debian package is missing or is not a regular file: {raw_path!r}.")
        sources.append(source.resolve())

    if len(sources) > 1:
        engine.warn(
            "Local packages are installed one at a time; an error can leave earlier "
            "packages installed."
        )
    for source in sources:
        install_args = engine.argparse.Namespace(
            deb=str(source), dry_run=False, no_deps=False, yes=True,
            user=cfg.mode == "user", arch_system=cfg.mode == "system",
            expected_sha256="", sha256="", gpg_signature=None, keyring=None,
            requested_version=None, sandbox=False, snapshot=False, verify_after=True,
        )
        engine.cmd_install(install_args, cfg)


def _upgrade(engine: Any, cfg: Any, invocation: Invocation) -> None:
    engine._require_experimental(cfg)
    if invocation.operands:
        engine.fail(
            "apt upgrade does not take package names; use "
            "apt-get install --only-upgrade PACKAGE."
        )
    repositories = engine.load_repos(cfg)
    database = engine.load_db(cfg)
    indexes: dict[str, dict[str, Any]] = {}
    plans: list[tuple[str, str, dict[str, str], str]] = []
    unavailable = 0
    for package, installed in sorted(database.items()):
        repo_name = installed.get("apt_repository")
        if not isinstance(repo_name, str) or repo_name not in repositories:
            continue
        try:
            if repo_name not in indexes:
                indexes[repo_name] = engine._load_repo_index(cfg, repositories, repo_name)
            candidate = engine.experimental.newest_package(indexes[repo_name]["packages"], package)
        except engine.experimental.ExperimentalError as exc:
            engine.warn(str(exc))
            unavailable += 1
            continue
        if not candidate:
            continue
        current_version = str(installed.get("version", "0"))
        if engine.experimental.compare_debian_versions(candidate["Version"], current_version) <= 0:
            continue
        pin = engine.load_pins(cfg).get(package)
        if pin:
            engine.warn(f"Skipping {package}; version {pin} is pinned.")
            continue
        plans.append((package, repo_name, candidate, current_version))

    if unavailable:
        engine.fail(
            "Some package indexes are unavailable. No upgrades were applied; "
            "run apt-get update and retry."
        )
    if not plans:
        print("All DebArk applications tracked by the APT integration are up to date.")
        return

    print("DebArk will upgrade these APT-managed applications:")
    for package, repo_name, candidate, current_version in plans:
        print(f"  {package}: {current_version} → {candidate['Version']} ({repo_name})")
    if len(plans) > 1:
        print(
            "Each application is committed separately; an error may leave earlier "
            "successful items upgraded."
        )
    if not engine.confirm(
        "Continue with these DebArk upgrades?", invocation.assume_yes or cfg.auto_yes
    ):
        engine.warn("Cancelled.")
        return

    for package, repo_name, candidate, _current_version in plans:
        engine.cmd_repo(
            engine.argparse.Namespace(
                repo_command="install", name=repo_name, package=package,
                version=candidate["Version"], yes=True, no_deps=False, sandbox=False,
            ),
            cfg,
        )


def run(tool: str, tokens: list[str], cfg: Any) -> None:
    from . import engine

    if tokens in ([], ["-h"], ["--help"], ["help"]):
        commands = ", ".join(sorted(_ACTIONS[tool]))
        print(f"DebArk compatibility mode for {tool}")
        print(f"Supported operations: {commands}")
        print("This command operates on DebArk-managed applications only.")
        if tool == "dpkg":
            print(
                "dpkg -i installs local .deb files through DebArk; query operations "
                "are read-only and Debian maintainer scripts are never run."
            )
        return
    if tokens in (["-v"], ["--version"]):
        print(f"DebArk {engine.VERSION} ({tool} compatibility)")
        return

    invocation = _parse(tool, tokens, engine)
    previous_quiet = engine._OUTPUT_QUIET
    engine._OUTPUT_QUIET = previous_quiet or invocation.quiet
    try:
        if tool == "dpkg":
            if invocation.action == "install-local":
                _install_local_debs(engine, cfg, invocation)
            elif invocation.action == "architecture":
                print("amd64")
            elif invocation.action == "list":
                if not invocation.operands:
                    engine.cmd_list(engine.argparse.Namespace(), cfg)
                else:
                    database = engine.load_db(cfg)
                    for package in invocation.operands:
                        name, version = _package_spec(package, engine)
                        if version is not None:
                            engine.fail("dpkg --list accepts package names only.")
                        entry = database.get(name)
                        if entry:
                            print(f"{name}\t{entry.get('version', '?')}\tinstalled via DebArk")
                        else:
                            print(f"{name}\tnot installed via DebArk")
            elif invocation.action == "status":
                for package in invocation.operands:
                    name, version = _package_spec(package, engine)
                    if version is not None:
                        engine.fail("dpkg --status accepts package names only.")
                    engine.cmd_info(engine.argparse.Namespace(deb=name), cfg)
            else:
                for package in invocation.operands:
                    name, version = _package_spec(package, engine)
                    if version is not None:
                        engine.fail("dpkg --listfiles accepts package names only.")
                    engine.cmd_files(engine.argparse.Namespace(package=name), cfg)
            return

        if tool == "apt-cache":
            engine._require_experimental(cfg)
            if invocation.action == "search":
                _require_arguments(invocation, engine)
                engine.cmd_repo(
                    engine.argparse.Namespace(
                        repo_command="search", query=" ".join(invocation.operands), repo=None
                    ),
                    cfg,
                )
            else:
                _require_arguments(invocation, engine)
                for spec in invocation.operands:
                    package, version = _package_spec(spec, engine)
                    if version is not None:
                        engine.fail(
                            "apt-cache show and policy accept package names, "
                            "not version-qualified names."
                        )
                    print("DebArk repository information; APT priority policy is not implemented.")
                    engine.cmd_repo(
                        engine.argparse.Namespace(repo_command="info", package=package, repo=None),
                        cfg,
                    )
            return

        if invocation.action == "update":
            if invocation.operands:
                engine.fail("apt-get update does not accept package names.")
            engine.cmd_repo(
                engine.argparse.Namespace(repo_command="sync", name=None), cfg
            )
        elif invocation.action in {"install", "reinstall"}:
            _install(engine, cfg, invocation)
        elif invocation.action in {"remove", "purge"}:
            _remove(engine, cfg, invocation)
        elif invocation.action == "upgrade":
            _upgrade(engine, cfg, invocation)
        elif invocation.action == "list":
            if not invocation.installed_only or invocation.operands:
                engine.fail("Only apt list --installed is supported.")
            engine.cmd_list(engine.argparse.Namespace(), cfg)
    finally:
        engine._OUTPUT_QUIET = previous_quiet
