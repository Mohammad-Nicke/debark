"""Focused tests for DebArk's archive and integrity boundaries."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import gzip
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from debark import apt_compat, engine


def make_tar(entries: list[tuple[str, bytes | None, bytes | None]]) -> bytes:
    """Build a small tar archive from (name, data, linkname) entries."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, linkname in entries:
            member = tarfile.TarInfo(name)
            if linkname is not None:
                member.type = tarfile.SYMTYPE
                member.linkname = linkname
                archive.addfile(member)
            else:
                payload = data or b""
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


class ArchivePathTests(unittest.TestCase):
    def test_normalizes_dot_components(self) -> None:
        self.assertEqual(engine._archive_rel("usr/./bin/demo").as_posix(), "usr/bin/demo")

    def test_rejects_absolute_and_parent_paths(self) -> None:
        for name in ("/etc/passwd", "../outside", "usr/../../outside", "."):
            with self.subTest(name=name), self.assertRaises(engine.DebArkError):
                engine._archive_rel(name)

    def extract(self, payload: bytes, destination: Path) -> None:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            engine._safe_extract_tar(archive, destination)

    def test_extracts_regular_files_and_safe_links(self) -> None:
        payload = make_tar(
            [
                ("usr/bin/demo", b"demo", None),
                ("usr/bin/demo-link", None, "demo"),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "payload"
            self.extract(payload, destination)
            self.assertEqual((destination / "usr/bin/demo").read_bytes(), b"demo")
            self.assertTrue((destination / "usr/bin/demo-link").is_symlink())
            self.assertEqual((destination / "usr/bin/demo-link").read_bytes(), b"demo")

    def test_rejects_unsafe_archive_entries(self) -> None:
        for label in (
            "absolute path",
            "parent traversal",
            "escaping symlink",
            "symlink traversal",
            "duplicate path",
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                destination = root / "payload"
                outside = root / "escape"
                unsafe_archives = {
                    "absolute path": [(str(outside), b"bad", None)],
                    "parent traversal": [("../escape", b"bad", None)],
                    "escaping symlink": [("usr/link", None, "../../outside")],
                    "symlink traversal": [
                        ("usr/link", None, "target"),
                        ("usr/link/file", b"bad", None),
                    ],
                    "duplicate path": [("usr/file", b"one", None), ("usr/file", b"two", None)],
                }
                with self.assertRaises(engine.DebArkError):
                    self.extract(make_tar(unsafe_archives[label]), destination)
                self.assertFalse(outside.exists())

    def test_rejects_special_files(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            member = tarfile.TarInfo("usr/device")
            member.type = tarfile.FIFOTYPE
            archive.addfile(member)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(engine.DebArkError):
                self.extract(buffer.getvalue(), Path(temporary) / "payload")

    def test_rejects_escaping_hard_links(self) -> None:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            member = tarfile.TarInfo("usr/alias")
            member.type = tarfile.LNKTYPE
            member.linkname = "../../outside"
            archive.addfile(member)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(engine.DebArkError):
                self.extract(buffer.getvalue(), Path(temporary) / "payload")

    def test_regular_and_symlink_digests_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            regular = root / "file"
            regular.write_bytes(b"package data")
            self.assertEqual(
                engine.path_digest(regular), hashlib.sha256(b"package data").hexdigest()
            )

            link = root / "link"
            link.symlink_to("file")
            expected = hashlib.sha256(b"symlink:file").hexdigest()
            self.assertEqual(engine.path_digest(link), expected)

    def test_verify_command_detects_modified_managed_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            managed_file = Path(temporary) / "managed-file"
            managed_file.write_bytes(b"changed")
            record = {
                "path": str(managed_file),
                "sha256": hashlib.sha256(b"original").hexdigest(),
            }
            args = type("Args", (), {"package": "fixture"})()
            config = object()
            with (
                patch.object(engine, "load_db", return_value={"fixture": {}}),
                patch.object(engine, "manifest_files", return_value=[record]),
                patch.object(engine, "warn"),
                self.assertRaises(engine.DebArkError),
            ):
                engine.cmd_verify(args, config)


class PackageAndDependencyTests(unittest.TestCase):
    def test_dependency_map_handles_arch_suffix_and_local_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            mapping_file = Path(temporary) / "dependency-map.json"
            mapping_file.write_text(json.dumps({"libexample1": "example-arch"}), encoding="utf-8")
            config = object.__new__(engine.Config)
            config.depmap_file = mapping_file
            with patch.object(engine, "_repo_packages", return_value=set()):
                self.assertEqual(engine.map_dep("libc6:amd64", config), "glibc")
                self.assertEqual(engine.map_dep("libexample1", config), "example-arch")
                self.assertIsNone(engine.map_dep("unknown-debian-package", config))

    def extract_synthetic_deb(
        self,
        root: Path,
        control: bytes,
        payload_file: str,
        payload: bytes,
        postinst: bytes | None = None,
    ) -> tuple[dict[str, str], Path]:
        control_entries = [("./control", control, None)]
        if postinst is not None:
            control_entries.append(("./postinst", postinst, None))
        control_archive = root / "control.tar.gz"
        control_archive.write_bytes(make_tar(control_entries))
        data_archive = root / "data.tar.gz"
        data_archive.write_bytes(make_tar([(payload_file, payload, None)]))
        package = root / "fixture.deb"
        package.write_bytes(b"synthetic test fixture")
        workdir = root / "work"
        workdir.mkdir()
        members = {"control.tar.gz": control_archive, "data.tar.gz": data_archive}

        def copy_member(_package: Path, member: str, target: Path) -> None:
            shutil.copyfile(members[member], target)

        with (
            patch.object(
                engine,
                "_run_checked",
                return_value=CompletedProcess(
                    args=["ar", "t", str(package)],
                    returncode=0,
                    stdout=b"debian-binary\ncontrol.tar.gz\ndata.tar.gz\n",
                    stderr=b"",
                ),
            ),
            patch.object(engine, "_write_ar_member", side_effect=copy_member),
        ):
            return engine.extract_deb(package, workdir)

    def test_synthetic_deb_without_dependencies_extracts_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            control = b"Package: debark-simple\nVersion: 1.0\nArchitecture: amd64\n"
            metadata, payload = self.extract_synthetic_deb(
                Path(temporary), control, "./usr/share/debark/message", b"hello"
            )
            self.assertEqual(metadata["Package"], "debark-simple")
            self.assertNotIn("Depends", metadata)
            self.assertEqual((payload / "usr/share/debark/message").read_bytes(), b"hello")

    def test_synthetic_deb_with_dependency_never_runs_maintainer_script(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "maintainer-script-ran"
            control = (
                "Package: debark-fixture\n"
                "Version: 1.0\n"
                "Architecture: amd64\n"
                "Depends: libc6 (>= 2.31)\n"
            ).encode()
            script = f"#!/bin/sh\ntouch {marker}\n".encode()
            metadata, payload = self.extract_synthetic_deb(
                root, control, "./usr/bin/fixture", b"fixture", postinst=script
            )

            self.assertEqual(metadata["Package"], "debark-fixture")
            self.assertEqual(metadata["Depends"], "libc6 (>= 2.31)")
            self.assertEqual((payload / "usr/bin/fixture").read_bytes(), b"fixture")
            self.assertFalse(marker.exists())


class InstallLifecycleTests(unittest.TestCase):
    def test_cli_help_covers_default_apt_shortcuts_and_original_tools(self) -> None:
        help_text = engine.build_parser().format_help()
        for term in ("search", "config", "apt-get", "apt-real", "dpkg-real", "/usr/local/bin"):
            with self.subTest(term=term):
                self.assertIn(term, help_text)

    def test_user_shortcut_to_system_debark_stays_in_system_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            system_command = root / "usr/local/bin/debark"
            user_command = root / "home/test/.local/bin/debark"
            system_command.parent.mkdir(parents=True)
            user_command.parent.mkdir(parents=True)
            system_command.write_text("system entry point\n", encoding="utf-8")
            system_command.chmod(0o755)
            user_command.symlink_to(system_command)
            with (
                patch.object(engine, "current_user_home", return_value=user_command.parents[2]),
                patch.object(engine.shutil, "which", return_value=str(user_command)),
                patch.object(engine.sys, "argv", [str(user_command)]),
            ):
                self.assertFalse(engine._running_from_user_install())
                with patch.object(engine, "_root_managed_path", return_value=True):
                    self.assertEqual(engine._trusted_system_program(), system_command)

    def make_deb(
        self,
        root: Path,
        marker: Path,
        version: str = "1.0",
        payload: bytes = b"#!/bin/sh\nprintf 'fixture\\n'\n",
    ) -> Path:
        control_archive = root / "control.tar.gz"
        control_archive.write_bytes(
            make_tar(
                [
                    (
                        "./control",
                        (
                            f"Package: debark-fixture\nVersion: {version}\n"
                            "Architecture: amd64\nDepends: libc6 (>= 2.31)\n"
                        ).encode(),
                        None,
                    ),
                    ("./postinst", f"#!/bin/sh\ntouch {marker}\n".encode(), None),
                ]
            )
        )
        data_archive = root / "data.tar.gz"
        data_buffer = io.BytesIO()
        with tarfile.open(fileobj=data_buffer, mode="w:gz") as archive:
            member = tarfile.TarInfo("./usr/bin/fixture")
            member.size = len(payload)
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(payload))
        data_archive.write_bytes(data_buffer.getvalue())

        package = root / f"debark-fixture-{version}.deb"
        (root / "debian-binary").write_bytes(b"2.0\n")
        subprocess.run(
            ["ar", "cr", str(package), "debian-binary", control_archive.name, data_archive.name],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return package

    def test_real_deb_install_and_remove_stay_in_temporary_user_paths(self) -> None:
        if not shutil.which("ar"):
            self.skipTest("binutils ar is required to exercise the real .deb container")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir()
            marker = root / "maintainer-script-ran"
            package = self.make_deb(root, marker)

            config = object.__new__(engine.Config)
            config.mode = "user"
            config.home_dir = home
            config.set_paths()
            config.auto_yes = False
            config.colors = False
            config.threads = 1
            install_args = engine.build_parser().parse_args(
                ["install", "--user", "--no-deps", "-y", str(package)]
            )
            real_run = subprocess.run

            def isolated_run(command: list[str], *args: object, **kwargs: object):
                if command and command[0] == "pacman":
                    return CompletedProcess(command, 0, stdout=b"glibc\n", stderr=b"")
                return real_run(command, *args, **kwargs)

            with (
                patch.object(engine, "analyze_elf_dependencies", return_value={}),
                patch.object(engine, "confirm", return_value=True),
                patch.object(engine, "run_hook", return_value=[]),
                patch.object(engine.subprocess, "run", side_effect=isolated_run),
            ):
                engine.cmd_install(install_args, config)

            app_file = config.apps_root / "debark-fixture/usr/bin/fixture"
            launcher = Path(engine.load_db(config)["debark-fixture"]["exec_path"])
            self.assertEqual(app_file.read_bytes(), b"#!/bin/sh\nprintf 'fixture\\n'\n")
            self.assertTrue(launcher.is_file())
            self.assertTrue(launcher.stat().st_mode & 0o111)
            self.assertFalse(marker.exists(), "Debian maintainer scripts must remain data only")
            self.assertTrue(
                all(
                    Path(record["path"]).is_relative_to(root)
                    for record in engine.manifest_files(engine.load_db(config)["debark-fixture"])
                )
            )

            remove_args = engine.build_parser().parse_args(
                ["remove", "--user", "-y", "debark-fixture"]
            )
            with patch.object(engine, "confirm", return_value=True):
                engine.cmd_remove(remove_args, config)

            self.assertFalse(config.apps_root.joinpath("debark-fixture").exists())
            self.assertFalse(launcher.exists())
            self.assertNotIn("debark-fixture", engine.load_db(config))
            self.assertFalse(marker.exists())

    def test_apt_updater_commands_reinstall_local_deb_through_debark(self) -> None:
        if not shutil.which("ar"):
            self.skipTest("binutils ar is required to exercise real .deb files")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir()
            marker = root / "maintainer-script-ran"
            old_package_dir = root / "old"
            new_package_dir = root / "new"
            old_package_dir.mkdir()
            new_package_dir.mkdir()
            old_package = self.make_deb(old_package_dir, marker, version="1.0")
            new_payload = b"#!/bin/sh\nprintf 'fixture updated\\n'\n"
            new_package = self.make_deb(new_package_dir, marker, version="1.1", payload=new_payload)

            config = object.__new__(engine.Config)
            config.mode = "user"
            config.home_dir = home
            config.set_paths()
            config.auto_yes = False
            config.colors = False
            config.threads = 1

            wrapper_dir = root / "bin"
            wrapper_dir.mkdir()
            entrypoint = Path(engine.__file__).parents[2] / "debark"
            for name in ("apt-get", "dpkg"):
                (wrapper_dir / name).symlink_to(entrypoint)
            lookup = subprocess.run(
                ["bash", "-c", "command -v apt-get && command -v dpkg"],
                check=True,
                capture_output=True,
                text=True,
                env={"PATH": f"{wrapper_dir}{os.pathsep}{os.defpath}"},
            )
            self.assertEqual(
                lookup.stdout.splitlines(),
                [str(wrapper_dir / "apt-get"), str(wrapper_dir / "dpkg")],
            )

            real_run = subprocess.run

            def isolated_run(command: list[str], *args: object, **kwargs: object):
                if command and command[0] == "pacman":
                    return CompletedProcess(command, 0, stdout=b"glibc\n", stderr=b"")
                return real_run(command, *args, **kwargs)

            with (
                patch.object(engine, "analyze_elf_dependencies", return_value={}),
                patch.object(engine, "confirm", return_value=True),
                patch.object(engine, "run_hook", return_value=[]),
                patch.object(engine.subprocess, "run", side_effect=isolated_run),
            ):
                apt_compat.run("apt-get", ["install", "-f", "-y"], config)
                apt_compat.run("dpkg", ["-i", str(old_package)], config)
                apt_compat.run("dpkg", ["-i", str(new_package)], config)

            installed = engine.load_db(config)["debark-fixture"]
            self.assertEqual(installed["version"], "1.1")
            self.assertEqual(
                (config.apps_root / "debark-fixture/usr/bin/fixture").read_bytes(),
                new_payload,
            )
            self.assertFalse(marker.exists(), "Debian maintainer scripts must remain data only")
            self.assertTrue(
                apt_compat._parse("apt-get", ["-f", "install", "-y"], engine).fix_broken
            )

    def test_apt_compat_requests_root_only_for_system_changes(self) -> None:
        self.assertFalse(apt_compat.needs_system_access("apt", ["--help"]))
        self.assertFalse(apt_compat.needs_system_access("apt", ["list", "--installed"]))
        self.assertFalse(apt_compat.needs_system_access("apt", ["list", "install"]))
        self.assertFalse(apt_compat.needs_system_access("apt-cache", ["search", "demo"]))
        self.assertTrue(apt_compat.needs_system_access("apt-get", ["update"]))
        self.assertTrue(apt_compat.needs_system_access("apt-get", ["install", "-f", "-y"]))
        self.assertTrue(apt_compat.needs_system_access("dpkg", ["-i", "demo.deb"]))
        self.assertFalse(apt_compat.needs_system_access("dpkg", ["--unpack", "demo.deb"]))

    def test_real_tool_aliases_are_controlled_and_forward_mutations_to_root(self) -> None:
        self.assertTrue(apt_compat.real_needs_system_access("apt", ["install", "demo"]))
        self.assertFalse(apt_compat.real_needs_system_access("apt", ["--version"]))
        self.assertTrue(apt_compat.real_needs_system_access("dpkg", ["--unpack", "demo.deb"]))
        with (
            patch.dict(
                apt_compat.os.environ, {"PATH": "/home/test/.local/bin:/usr/local/bin:/usr/bin"}
            ),
            patch.object(apt_compat.os, "execv") as execv,
        ):
            apt_compat.run_real("apt", ["--version"])
            self.assertEqual(
                apt_compat.os.environ["PATH"],
                "/usr/bin:/bin:/home/test/.local/bin:/usr/local/bin",
            )
        execv.assert_called_once_with("/usr/bin/apt", ["/usr/bin/apt", "--version"])
        self.assertEqual(
            engine.normalize_command_shortcuts(["real-apt", "--version"]),
            ["real-tool", "apt", "--version"],
        )
        self.assertEqual(
            engine.normalize_command_shortcuts(["apt-real", "--version"]),
            ["real-tool", "apt", "--version"],
        )

    def test_system_command_requests_privilege_without_interactive_stdin(self) -> None:
        if not any(Path(path).is_file() for path in ("/usr/bin/sudo", "/usr/bin/doas")):
            self.skipTest("sudo or doas is required to exercise the privilege request")
        with tempfile.TemporaryDirectory() as temporary:
            program = Path(temporary) / "debark"
            program.write_text("trusted test entry point\n", encoding="utf-8")
            program.chmod(0o755)
            result = CompletedProcess(["sudo"], 0)
            with (
                patch.object(engine, "_trusted_system_program", return_value=program),
                patch.object(engine, "_root_managed_path", return_value=True),
                patch.object(engine.os, "access", return_value=True),
                patch.object(engine.sys, "stdin", io.StringIO("")),
                patch.object(engine.sys, "argv", ["debark", "apt-compat", "apt-get", "update"]),
                patch.object(engine.sys, "stderr", io.StringIO()),
                patch.object(engine.subprocess, "run", return_value=result) as run,
            ):
                self.assertEqual(engine.request_system_access(), 0)
            self.assertIn("apt-compat", run.call_args.args[0])


class ExperimentalFeatureTests(unittest.TestCase):
    def test_debian_versions_follow_epoch_and_tilde_ordering(self) -> None:
        compare = engine.experimental.compare_debian_versions
        self.assertLess(compare("1.0~rc1-1", "1.0-1"), 0)
        self.assertLess(compare("1.0-1", "1.0-2"), 0)
        self.assertLess(compare("1:1.0-1", "2:0.1-1"), 0)
        self.assertEqual(compare("1.02-1", "1.2-1"), 0)

    def test_release_index_sync_checks_signature_digest_and_path(self) -> None:
        package_index = (
            "Package: fixture\nVersion: 1.0-1\nArchitecture: amd64\n"
            "Filename: pool/main/f/fixture.deb\nSize: 12\nSHA256: " + "a" * 64 + "\n\n"
        ).encode()
        compressed_index = gzip.compress(package_index)
        digest = hashlib.sha256(compressed_index).hexdigest()
        now = datetime.now(timezone.utc)
        release = (
            f"Suite: bookworm\nArchitectures: amd64\nComponents: main\n"
            f"Date: {format_datetime(now)}\n"
            f"Valid-Until: {format_datetime(now + timedelta(days=7))}\n"
            "SHA256:\n "
            f"{digest} {len(compressed_index)} main/binary-amd64/Packages.gz\n"
        ).encode()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            keyring = root / "trusted.gpg"
            keyring.write_bytes(b"test keyring")
            responses = {
                "/Release": release,
                "/Release.gpg": b"signature",
                "/Packages.gz": compressed_index,
            }

            def fetch(url: str, _limit: int) -> bytes:
                return responses[next(suffix for suffix in responses if url.endswith(suffix))]

            with (
                patch.object(engine.experimental, "fetch_bytes", side_effect=fetch),
                patch.object(
                    engine.experimental.subprocess,
                    "run",
                    return_value=CompletedProcess(
                        args=["gpgv"], returncode=0, stdout=b"", stderr=b""
                    ),
                ),
            ):
                result = engine.experimental.sync_repository(
                    {
                        "name": "fixture",
                        "url": "https://packages.example.test",
                        "suite": "bookworm",
                        "components": ["main"],
                        "keyring": str(keyring),
                    },
                    root / "cache",
                )
            self.assertEqual(result["package_count"], 1)
            cached = engine.experimental.load_synced_packages(root / "cache", "fixture")
            self.assertEqual(cached["packages"][0]["Package"], "fixture")

            responses["/Packages.gz"] = compressed_index + b"tampered"
            with (
                patch.object(engine.experimental, "fetch_bytes", side_effect=fetch),
                patch.object(
                    engine.experimental.subprocess,
                    "run",
                    return_value=CompletedProcess(
                        args=["gpgv"], returncode=0, stdout=b"", stderr=b""
                    ),
                ),
                self.assertRaises(engine.experimental.ExperimentalError),
            ):
                engine.experimental.sync_repository(
                    {
                        "name": "fixture",
                        "url": "https://packages.example.test",
                        "suite": "bookworm",
                        "components": ["main"],
                        "keyring": str(keyring),
                    },
                    root / "cache",
                )

    def test_cve_report_compares_fixed_debian_version(self) -> None:
        database = {"openssl": {"version": "3.0.10-1"}}
        tracker = {
            "openssl": {
                "CVE-2026-1234": {
                    "description": "Example advisory",
                    "releases": {"bookworm": {"status": "resolved", "fixed_version": "3.0.11-1"}},
                }
            }
        }
        findings = engine.experimental.package_cves(database, tracker, "bookworm")
        self.assertEqual(findings[0]["cve"], "CVE-2026-1234")
        self.assertEqual(findings[0]["status"], "affected")

    def test_release_dates_without_timezone_are_rejected(self) -> None:
        with self.assertRaises(engine.experimental.ExperimentalError):
            engine.experimental.validate_release_dates({"Date": "Mon, 05 Oct 2026 12:00:00"})

    def test_network_download_errors_are_reported_as_experimental_errors(self) -> None:
        entry = {"Filename": "pool/main/f/fixture.deb", "Size": "4", "SHA256": "a" * 64}
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch.object(
                    engine.experimental.urllib.request,
                    "urlopen",
                    side_effect=OSError("offline"),
                ),
                self.assertRaises(engine.experimental.ExperimentalError),
            ):
                engine.experimental.download_package(
                    "https://packages.example.test", entry, Path(temporary) / "fixture.deb"
                )

    def test_cached_apt_indexes_expire_and_match_their_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            index_path = cache / "apt/fixture/packages.json"
            index_path.parent.mkdir(parents=True)
            index_path.write_text(
                json.dumps(
                    {
                        "repository": "fixture",
                        "architecture": "amd64",
                        "synced_at": 100,
                        "valid_until": 200,
                        "packages": [],
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                engine.experimental.load_synced_packages(cache, "fixture", now=150)["packages"], []
            )
            with self.assertRaises(engine.experimental.ExperimentalError):
                engine.experimental.load_synced_packages(cache, "fixture", now=201)
            index_path.write_text(
                json.dumps(
                    {
                        "repository": "another",
                        "architecture": "amd64",
                        "synced_at": 100,
                        "valid_until": 200,
                        "packages": [],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(engine.experimental.ExperimentalError):
                engine.experimental.load_synced_packages(cache, "fixture", now=150)


class ConfigTests(unittest.TestCase):
    def test_experimental_setting_parses_boolean_strings_without_truthiness_bug(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = object.__new__(engine.Config)
            config.cfg_file = Path(temporary) / "config.json"
            config.auto_yes = False
            config.colors = True
            config.experimental_features = False
            config.threads = 4

            config.cfg_file.write_text('{"experimental_features":"false"}', encoding="utf-8")
            config._load()
            self.assertFalse(config.experimental_features)

            config.cfg_file.write_text('{"experimental_features":"true"}', encoding="utf-8")
            config._load()
            self.assertTrue(config.experimental_features)


class SandboxWrapperTests(unittest.TestCase):
    def test_sandbox_launcher_isolates_home_network_and_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir()
            app_root = root / "app"
            executable = app_root / "usr/bin/demo"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper = root / "bin/demo"

            engine.make_wrapper(
                wrapper,
                app_root,
                executable,
                object(),
                payload,
                sandbox=True,
            )
            content = wrapper.read_text(encoding="utf-8")

        self.assertIn("exec env -i", content)
        self.assertIn("--net=none", content)
        self.assertIn("--private-tmp", content)
        self.assertIn("--read-only=/", content)
        self.assertIn("--caps.drop=all", content)
        self.assertIn("exec bwrap \\", content)
        self.assertIn("--die-with-parent", content)
        self.assertIn("--unshare-all", content)
        self.assertIn("--ro-bind / /", content)
        self.assertIn("--dev /dev", content)
        self.assertIn("--proc /proc", content)
        self.assertIn("--tmpfs /home", content)
        self.assertIn("--tmpfs /root", content)
        self.assertIn("--tmpfs /run/user", content)
        self.assertIn("--clearenv", content)
        self.assertIn("--setenv PATH", content)
        self.assertIn("--dir /tmp/debark-home", content)
        self.assertIn("--setenv HOME /tmp/debark-home", content)
        self.assertIn("unset DBUS_SESSION_BUS_ADDRESS SSH_AUTH_SOCK", content)
        self.assertIn("XDG_RUNTIME_DIR XDG_CONFIG_HOME", content)
        self.assertIn("exec env -i", content)
        self.assertIn("PATH=/usr/local/sbin:/usr/local/bin:/usr/bin:/bin", content)
        self.assertNotIn('--ro-bind "$HOME" "$HOME"', content)

    def test_bubblewrap_launcher_runs_with_isolated_environment_and_filesystem(self) -> None:
        bwrap = shutil.which("bwrap")
        if not bwrap:
            self.skipTest("bubblewrap is not installed")
        probe = subprocess.run(
            [
                bwrap,
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                "--ro-bind",
                "/",
                "/",
                "--dev",
                "/dev",
                "--proc",
                "/proc",
                "--tmpfs",
                "/tmp",
                "--tmpfs",
                "/home",
                "--tmpfs",
                "/root",
                "--tmpfs",
                "/run/user",
                "--",
                "/usr/bin/true",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if probe.returncode:
            self.skipTest("bubblewrap user/network namespaces are unavailable in this environment")

        try:
            temporary = tempfile.TemporaryDirectory(prefix="debark-sandbox-", dir="/var/tmp")
        except OSError:
            self.skipTest("/var/tmp is not writable for an isolated launcher fixture")
        with temporary:
            root = Path(temporary.name)
            app_root = root / "app"
            executable = app_root / "usr/bin/demo"
            executable.parent.mkdir(parents=True)
            executable.write_text(
                "#!/bin/sh\n"
                '[ "$HOME" = /tmp/debark-home ] || exit 10\n'
                '[ -z "${DEBARK_TEST_SECRET+x}" ] || exit 11\n'
                '[ -z "${LD_LIBRARY_PATH+x}" ] || exit 12\n'
                '[ -z "${XDG_RUNTIME_DIR+x}" ] || exit 13\n'
                '[ ! -e "$1" ] || exit 14\n'
                '[ ! -e "/run/user/$(id -u)" ] || exit 15\n'
                'touch "$HOME/sandbox-write-check" || exit 16\n'
                'if touch "$1" 2>/dev/null; then exit 17; fi\n'
                "printf 'sandbox-ok\\n'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            wrapper = root / "bin/demo"
            engine.make_wrapper(wrapper, app_root, executable, object(), app_root, sandbox=True)
            forbidden_host_file = root / "must-not-be-created"
            environment = os.environ.copy()
            environment["DEBARK_TEST_SECRET"] = "host-only-secret"
            environment["LD_LIBRARY_PATH"] = "/tmp/host-library-path"
            completed = subprocess.run(
                [str(wrapper), str(forbidden_host_file)],
                check=False,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "sandbox-ok")
            self.assertFalse(forbidden_host_file.exists())


if __name__ == "__main__":
    unittest.main()
