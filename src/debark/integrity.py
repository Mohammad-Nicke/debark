"""Verify the signed manifest for an official DebArk installation."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

from . import __url__, __version__

PUBLIC_KEY_SHA256 = "e6054fe47a50b5eb33c955cac256746abe86c23e3aa4aeaf85216a89918ee55f"
MANIFEST_NAME = "official.manifest"
SIGNATURE_NAME = "official.manifest.sig"
MANIFEST_HEADER = "DEBARK-OFFICIAL-MANIFEST 1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")


class IntegrityError(RuntimeError):
    """Raised when an installed DebArk copy cannot be verified."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_openssl(*arguments: str) -> bytes:
    if shutil.which("openssl") is None:
        raise IntegrityError("OpenSSL is missing; reinstall DebArk with the official installer.")
    try:
        result = subprocess.run(
            ["openssl", *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IntegrityError(f"OpenSSL verification could not run: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise IntegrityError(detail or "The signed release metadata is invalid.")
    return result.stdout


def _read_manifest(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntegrityError("The official release manifest is missing.") from exc
    if len(content) > 1024 * 1024:
        raise IntegrityError("The official release manifest is unexpectedly large.")

    lines = content.splitlines()
    if not lines or lines[0] != MANIFEST_HEADER:
        raise IntegrityError("The official release manifest has an unsupported format.")

    metadata: dict[str, str] = {}
    files: dict[str, str] = {}
    file_section = False
    for line in lines[1:]:
        if line == "--files--":
            if file_section:
                raise IntegrityError("The official release manifest repeats its file section.")
            file_section = True
            continue
        if not line:
            continue
        if not file_section:
            key, separator, value = line.partition("=")
            if not separator or key in metadata:
                raise IntegrityError("The official release manifest contains invalid metadata.")
            metadata[key] = value
            continue

        digest, separator, relative_name = line.partition("  ")
        relative = PurePosixPath(relative_name)
        if (not separator or not _SHA256.fullmatch(digest) or not relative_name
                or relative.is_absolute() or ".." in relative.parts
                or relative.as_posix() != relative_name or relative_name in files):
            raise IntegrityError("The official release manifest contains an unsafe file entry.")
        files[relative_name] = digest

    required = {"repository", "commit", "version", "archive_sha256", "root_kind"}
    if (not file_section or set(metadata) != required or not files
            or metadata["repository"] != __url__
            or not _COMMIT.fullmatch(metadata["commit"])
            or metadata["version"] != __version__
            or (metadata["archive_sha256"] != "none"
                and not _SHA256.fullmatch(metadata["archive_sha256"]))
            or metadata["root_kind"] not in {"source", "standalone"}):
        raise IntegrityError("The official release manifest does not match this DebArk version.")
    return metadata, files


def verify_official_copy(package_root: Path | None = None,
                         standalone: bool | None = None) -> None:
    """Verify the signed manifest and every file in an official distribution."""
    compiled_info = getattr(sys.modules.get(__name__), "__compiled__", None)
    if standalone is None:
        standalone = compiled_info is not None

    if standalone:
        containing_dir = getattr(compiled_info, "containing_dir", None)
        executable = Path(sys.argv[0]).resolve()
        root = (package_root or Path(containing_dir or executable.parent)).resolve()
        manifest = root / MANIFEST_NAME
        signature = root / SIGNATURE_NAME
        public_key = root / "debark-release-public.pem"
        expected_kind = "standalone"
    else:
        root = (package_root or Path(__file__).resolve().parent).resolve()
        data_dir = root / "data"
        manifest = data_dir / MANIFEST_NAME
        signature = data_dir / SIGNATURE_NAME
        public_key = data_dir / "debark-release-public.pem"
        expected_kind = "source"
    for path in (manifest, signature, public_key):
        if path.is_symlink() or not path.is_file():
            raise IntegrityError("Official signature files are missing or unsafe; reinstall DebArk.")

    key_der = _run_openssl("pkey", "-pubin", "-in", str(public_key), "-outform", "DER")
    if hashlib.sha256(key_der).hexdigest() != PUBLIC_KEY_SHA256:
        raise IntegrityError("The official release public key does not match the pinned key.")

    _run_openssl(
        "pkeyutl", "-verify", "-pubin", "-inkey", str(public_key), "-rawin",
        "-in", str(manifest), "-sigfile", str(signature),
    )
    metadata, signed_files = _read_manifest(manifest)
    if metadata["root_kind"] != expected_kind:
        raise IntegrityError("The signed release manifest is for a different DebArk build type.")

    excluded_files = ({MANIFEST_NAME, SIGNATURE_NAME} if standalone else {
        f"data/{MANIFEST_NAME}", f"data/{SIGNATURE_NAME}"
    })
    actual_files: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise IntegrityError(f"Unexpected symbolic link in DebArk installation: {relative}")
        if path.is_file() and relative.as_posix() not in excluded_files:
            actual_files.add(relative.as_posix())

    if actual_files != set(signed_files):
        raise IntegrityError("Installed DebArk files do not match the signed official release.")
    for relative_name, expected_digest in signed_files.items():
        file_path = root.joinpath(*PurePosixPath(relative_name).parts)
        if not file_path.is_file() or file_path.is_symlink():
            raise IntegrityError(f"An official DebArk file is missing: {relative_name}")
        digest = _sha256_file(file_path)
        if digest != expected_digest:
            raise IntegrityError(f"An official DebArk file was changed: {relative_name}")
