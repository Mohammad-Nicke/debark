"""Focused tests for DebArk's archive and integrity boundaries."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from debark import engine


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


class SandboxWrapperTests(unittest.TestCase):
    def test_sandbox_launcher_blocks_network_and_home_writes(self) -> None:
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

        self.assertIn('firejail --net=none --read-only="$HOME"', content)
        self.assertIn('bwrap --die-with-parent --unshare-all', content)
        self.assertIn('--ro-bind "$HOME" "$HOME"', content)
        self.assertNotIn('--bind "$HOME" "$HOME"', content)


if __name__ == "__main__":
    unittest.main()
