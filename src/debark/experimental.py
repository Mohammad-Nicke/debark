"""Opt-in, experimental Debian repository and security-data helpers."""

from __future__ import annotations

import bz2
import hashlib
import json
import lzma
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import cmp_to_key
from pathlib import Path, PurePosixPath
from typing import Any

USER_AGENT = "DebArk/0.1 (+https://github.com/Mohammad-Nicke/debark)"
TRACKER_JSON_URL = "https://security-tracker.debian.org/tracker/data/json"
MAX_RELEASE_BYTES = 4 * 1024 * 1024
MAX_INDEX_BYTES = 96 * 1024 * 1024
MAX_PACKAGES_BYTES = 512 * 1024 * 1024
MAX_LZMA_MEMORY = 256 * 1024 * 1024
MAX_PACKAGE_BYTES = 1024 * 1024 * 1024
MAX_TRACKER_BYTES = 192 * 1024 * 1024


class ExperimentalError(Exception):
    """Raised when an experimental network operation cannot be trusted or used."""


def _https_url(url: str, base: str | None = None) -> str:
    resolved = urllib.parse.urljoin(base, url) if base else url
    parsed = urllib.parse.urlsplit(resolved)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ExperimentalError("Experimental repository requests require a credential-free HTTPS URL.")
    if parsed.fragment:
        raise ExperimentalError("Repository URLs must not contain fragments.")
    if parsed.query:
        raise ExperimentalError("Repository URLs must not contain query strings.")
    return resolved


def fetch_bytes(url: str, limit: int) -> bytes:
    url = _https_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            _https_url(response.geturl())
            declared_size = response.headers.get("Content-Length")
            if declared_size and int(declared_size) > limit:
                raise ExperimentalError(f"Remote file exceeds the {limit}-byte safety limit.")
            output = bytearray()
            while chunk := response.read(min(1024 * 1024, limit + 1 - len(output))):
                output.extend(chunk)
                if len(output) > limit:
                    raise ExperimentalError(f"Remote file exceeds the {limit}-byte safety limit.")
            return bytes(output)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise ExperimentalError(f"Could not download {url}: {exc}") from exc


def parse_control_stanzas(text: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        fields: dict[str, str] = {}
        key: str | None = None
        values: list[str] = []
        for line in paragraph.splitlines():
            if line[:1] in (" ", "\t") and key:
                values.append(line[1:])
            elif ":" in line:
                if key:
                    fields[key] = "\n".join(values).strip()
                key, value = line.split(":", 1)
                key, values = key.strip(), [value.strip()]
        if key:
            fields[key] = "\n".join(values).strip()
        if fields:
            records.append(fields)
    return records


def parse_release_sha256(release_text: str) -> dict[str, tuple[str, int]]:
    fields = parse_control_stanzas(release_text)
    if not fields:
        raise ExperimentalError("The signed Release file is empty or malformed.")
    lines = fields[0].get("SHA256", "").splitlines()
    result: dict[str, tuple[str, int]] = {}
    for line in lines:
        parts = line.split()
        if len(parts) != 3:
            continue
        digest, size_text, name = parts
        if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            continue
        if not size_text.isdecimal():
            continue
        relative = PurePosixPath(name)
        if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
            continue
        result[relative.as_posix()] = (digest.lower(), int(size_text))
    if not result:
        raise ExperimentalError("The signed Release file has no usable SHA256 entries.")
    return result


def validate_release_dates(fields: dict[str, str], now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    try:
        release_date = parsedate_to_datetime(fields["Date"])
        if release_date.tzinfo is None:
            raise ValueError("Date must include a timezone")
        release_date = release_date.astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise ExperimentalError("Signed Release metadata has no valid Date field.") from exc
    if release_date > now + timedelta(hours=24):
        raise ExperimentalError("Signed Release metadata is dated too far in the future.")
    valid_until_value = fields.get("Valid-Until")
    if valid_until_value:
        try:
            valid_until = parsedate_to_datetime(valid_until_value)
            if valid_until.tzinfo is None:
                raise ValueError("Valid-Until must include a timezone")
            valid_until = valid_until.astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise ExperimentalError("Signed Release metadata has an invalid Valid-Until field.") from exc
        if valid_until < now:
            raise ExperimentalError("Signed Release metadata has expired; refusing a replayed index.")
        return valid_until
    elif now - release_date > timedelta(days=14):
        raise ExperimentalError("Release metadata without Valid-Until is older than 14 days.")
    return release_date + timedelta(days=14)


def _decompress_index(name: str, payload: bytes) -> bytes:
    try:
        if name.endswith(".xz"):
            decoder = lzma.LZMADecompressor(memlimit=MAX_LZMA_MEMORY)
            result = decoder.decompress(payload, max_length=MAX_PACKAGES_BYTES + 1)
            if len(result) > MAX_PACKAGES_BYTES or not decoder.eof or decoder.unused_data:
                raise ExperimentalError("Decompressed Packages index exceeds the safety limit.")
            return result
        if name.endswith(".gz"):
            decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
            result = decoder.decompress(payload, MAX_PACKAGES_BYTES + 1)
            if (len(result) > MAX_PACKAGES_BYTES or decoder.unconsumed_tail
                    or not decoder.eof or decoder.unused_data):
                raise ExperimentalError("Decompressed Packages index exceeds the safety limit.")
            return result
        if name.endswith(".bz2"):
            decoder = bz2.BZ2Decompressor()
            result = decoder.decompress(payload, max_length=MAX_PACKAGES_BYTES + 1)
            if len(result) > MAX_PACKAGES_BYTES or not decoder.eof or decoder.unused_data:
                raise ExperimentalError("Decompressed Packages index exceeds the safety limit.")
            return result
        return payload
    except (OSError, EOFError, lzma.LZMAError, zlib.error) as exc:
        raise ExperimentalError(f"Could not decompress {name}: {exc}") from exc


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _release_text(url: str, keyring: Path, cache: Path) -> tuple[str, bytes]:
    release_url = _https_url("Release", url.rstrip("/") + "/")
    signature_url = _https_url("Release.gpg", url.rstrip("/") + "/")
    release = fetch_bytes(release_url, MAX_RELEASE_BYTES)
    signature = fetch_bytes(signature_url, MAX_RELEASE_BYTES)
    cache.mkdir(parents=True, exist_ok=True)
    release_path = cache / "Release"
    signature_path = cache / "Release.gpg"
    _write_atomic(release_path, release)
    _write_atomic(signature_path, signature)
    try:
        result = subprocess.run(
            ["gpgv", "--keyring", str(keyring), str(signature_path), str(release_path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ExperimentalError("gpgv is required for experimental APT sync; install gnupg first.") from exc
    except OSError as exc:
        raise ExperimentalError(f"Could not run gpgv to verify Release metadata: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise ExperimentalError(f"Release signature verification failed: {detail or 'untrusted key'}")
    try:
        text = release.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExperimentalError("The signed Release file is not valid UTF-8.") from exc
    return release_url, text


def sync_repository(repo: dict[str, Any], cache: Path, architecture: str = "amd64") -> dict[str, Any]:
    name = str(repo.get("name", ""))
    base = _https_url(str(repo.get("url", ""))).rstrip("/") + "/"
    suite = str(repo.get("suite", ""))
    components = repo.get("components", [])
    keyring_value = repo.get("keyring")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", name):
        raise ExperimentalError("Invalid repository name in configuration.")
    if not suite or any(part in (".", "..") for part in PurePosixPath(suite).parts):
        raise ExperimentalError("Invalid Debian suite in repository configuration.")
    if not isinstance(components, list) or not components:
        raise ExperimentalError("The repository has no configured components.")
    if architecture != "amd64":
        raise ExperimentalError("Experimental APT integration currently supports x86_64/amd64 only.")
    if not isinstance(keyring_value, str) or not keyring_value:
        raise ExperimentalError(f"Repository {name} has no trusted keyring; add it again with --keyring.")
    keyring = Path(keyring_value)
    try:
        keyring.lstat()
    except OSError as exc:
        raise ExperimentalError(f"Cannot read trusted keyring {keyring}: {exc}") from exc
    if not keyring.is_file() or keyring.is_symlink():
        raise ExperimentalError("The trusted keyring must be a regular, non-symlink file.")

    dist_url = urllib.parse.urljoin(base, f"dists/{urllib.parse.quote(suite, safe='/')}/")
    repository_cache = cache / "apt" / name
    release_url, release_text = _release_text(dist_url, keyring, repository_cache)
    release_fields = parse_control_stanzas(release_text)[0]
    valid_until = validate_release_dates(release_fields)
    advertised_suites = {
        release_fields.get("Suite", ""), release_fields.get("Codename", "")
    }
    if suite not in advertised_suites:
        raise ExperimentalError(f"Signed Release metadata does not identify the requested suite {suite}.")
    if architecture not in release_fields.get("Architectures", "").split():
        raise ExperimentalError(f"Signed Release metadata does not list {architecture}.")
    advertised_components = set(release_fields.get("Components", "").split())
    if not set(components).issubset(advertised_components):
        raise ExperimentalError("Signed Release metadata does not list every configured component.")
    checksums = parse_release_sha256(release_text)
    packages: list[dict[str, str]] = []
    verified_indexes: list[str] = []
    for component in components:
        if not isinstance(component, str) or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", component):
            raise ExperimentalError(f"Invalid repository component: {component!r}")
        index_name = None
        compressed = b""
        for suffix in ("Packages.xz", "Packages.gz", "Packages.bz2", "Packages"):
            candidate = f"{component}/binary-{architecture}/{suffix}"
            if candidate in checksums:
                index_name = candidate
                break
        if index_name is None:
            continue
        digest, expected_size = checksums[index_name]
        index_url = _https_url(index_name, release_url)
        compressed = fetch_bytes(index_url, MAX_INDEX_BYTES)
        if len(compressed) != expected_size or hashlib.sha256(compressed).hexdigest() != digest:
            raise ExperimentalError(f"SHA256/size verification failed for {index_name}.")
        raw_index = _decompress_index(index_name, compressed)
        if len(raw_index) > MAX_PACKAGES_BYTES:
            raise ExperimentalError(f"Decompressed index {index_name} exceeds the safety limit.")
        for entry in parse_control_stanzas(raw_index.decode("utf-8", errors="replace")):
            if entry.get("Architecture") not in (architecture, "all"):
                continue
            if not entry.get("Package") or not entry.get("Version"):
                continue
            if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,127}", entry["Package"]):
                continue
            try:
                relative = PurePosixPath(entry.get("Filename", ""))
                if relative.is_absolute() or any(p in ("", ".", "..") for p in relative.parts):
                    continue
            except (TypeError, ValueError):
                continue
            size = entry.get("Size", "")
            package_hash = entry.get("SHA256", "")
            if not size.isdecimal() or int(size) <= 0:
                continue
            if not re.fullmatch(r"[a-fA-F0-9]{64}", package_hash):
                continue
            packages.append({
                key: entry[key] for key in (
                    "Package", "Version", "Architecture", "Filename", "Size", "SHA256",
                    "Source", "Depends", "Description",
                ) if key in entry
            })
        verified_indexes.append(index_name)
    if not verified_indexes:
        raise ExperimentalError("No compressed Packages index with a signed SHA256 was found.")
    if not packages:
        raise ExperimentalError("Verified repository indexes contained no usable packages.")

    manifest = {
        "repository": name,
        "url": base.rstrip("/"),
        "suite": suite,
        "architecture": architecture,
        "release_sha256": hashlib.sha256(release_text.encode()).hexdigest(),
        "valid_until": int(valid_until.timestamp()),
        "indexes": verified_indexes,
        "synced_at": int(time.time()),
        "packages": packages,
    }
    _write_atomic(repository_cache / "packages.json", json.dumps(manifest, ensure_ascii=False).encode())
    return {key: value for key, value in manifest.items() if key != "packages"} | {
        "package_count": len(packages)
    }


def load_synced_packages(cache: Path, name: str, now: float | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}", name):
        raise ExperimentalError("Invalid repository name.")
    path = cache / "apt" / name / "packages.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExperimentalError(f"No verified package index is cached for {name}; run debark repo sync {name}.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("packages"), list):
        raise ExperimentalError(f"The cached index for {name} is malformed.")
    if value.get("repository") != name or value.get("architecture") != "amd64":
        raise ExperimentalError(f"The cached index for {name} has mismatched repository metadata.")
    now = time.time() if now is None else now
    try:
        synced_at = int(value["synced_at"])
        valid_until = int(value.get("valid_until", synced_at + 14 * 24 * 60 * 60))
    except (KeyError, TypeError, ValueError) as exc:
        raise ExperimentalError(f"The cached index for {name} has invalid freshness metadata.") from exc
    if synced_at > now + 600 or valid_until <= now:
        raise ExperimentalError(f"The cached index for {name} has expired; run debark repo sync {name}.")
    return value


def compare_debian_versions(left: str, right: str) -> int:
    def split(value: str) -> tuple[int, str, str]:
        epoch = 0
        if ":" in value:
            epoch_text, value = value.split(":", 1)
            if not epoch_text.isdecimal():
                raise ExperimentalError(f"Invalid Debian version epoch: {epoch_text}")
            epoch = int(epoch_text)
        upstream, separator, revision = value.rpartition("-")
        if not separator:
            upstream, revision = value, "0"
        if not upstream or not revision:
            raise ExperimentalError(f"Invalid Debian package version: {value}")
        return epoch, upstream, revision

    def order(char: str | None) -> int:
        if char == "~":
            return -1
        if char is None:
            return 0
        if char.isalpha():
            return ord(char)
        return ord(char) + 256

    def verrevcmp(a: str, b: str) -> int:
        i = j = 0
        while i < len(a) or j < len(b):
            while ((i < len(a) and not a[i].isdigit()) or
                   (j < len(b) and not b[j].isdigit())):
                ca = a[i] if i < len(a) and not a[i].isdigit() else None
                cb = b[j] if j < len(b) and not b[j].isdigit() else None
                if order(ca) != order(cb):
                    return -1 if order(ca) < order(cb) else 1
                if ca is not None:
                    i += 1
                if cb is not None:
                    j += 1
            while i < len(a) and a[i] == "0":
                i += 1
            while j < len(b) and b[j] == "0":
                j += 1
            start_i, start_j = i, j
            while i < len(a) and a[i].isdigit():
                i += 1
            while j < len(b) and b[j].isdigit():
                j += 1
            digits_a, digits_b = a[start_i:i], b[start_j:j]
            if len(digits_a) != len(digits_b):
                return -1 if len(digits_a) < len(digits_b) else 1
            if digits_a != digits_b:
                return -1 if digits_a < digits_b else 1
        return 0

    left_epoch, left_upstream, left_revision = split(left)
    right_epoch, right_upstream, right_revision = split(right)
    if left_epoch != right_epoch:
        return -1 if left_epoch < right_epoch else 1
    upstream_result = verrevcmp(left_upstream, right_upstream)
    return upstream_result or verrevcmp(left_revision, right_revision)


def newest_package(packages: list[dict[str, Any]], name: str,
                   version: str | None = None) -> dict[str, Any] | None:
    candidates = [item for item in packages if item.get("Package") == name
                  and (version is None or item.get("Version") == version)]
    if not candidates:
        return None
    try:
        return max(candidates, key=cmp_to_key(
            lambda left, right: compare_debian_versions(left["Version"], right["Version"])
        ))
    except (KeyError, ExperimentalError):
        raise ExperimentalError(f"Cannot compare repository versions for {name}.") from None


def download_package(repo_url: str, entry: dict[str, Any], destination: Path) -> Path:
    filename = entry.get("Filename", "")
    relative = PurePosixPath(filename)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise ExperimentalError("The package index contains an unsafe Filename path.")
    url = _https_url(relative.as_posix(), repo_url.rstrip("/") + "/")
    base = urllib.parse.urlsplit(repo_url)
    target = urllib.parse.urlsplit(url)
    if (base.scheme, base.hostname, base.port) != (target.scheme, target.hostname, target.port):
        raise ExperimentalError("The package Filename points to a different server.")
    try:
        expected_size = int(entry["Size"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ExperimentalError("The package index has an invalid Size.") from exc
    expected_hash = str(entry.get("SHA256", "")).lower()
    if expected_size < 1 or expected_size > MAX_PACKAGE_BYTES:
        raise ExperimentalError("The package size is outside the experimental safety limit.")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise ExperimentalError("The package index has no valid SHA256 digest.")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(request, timeout=30) as response:
            final_url = urllib.parse.urlsplit(_https_url(response.geturl()))
            if (base.scheme, base.hostname, base.port) != (
                final_url.scheme, final_url.hostname, final_url.port
            ):
                raise ExperimentalError("The package download redirected to a different server.")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > expected_size or size > MAX_PACKAGE_BYTES:
                    raise ExperimentalError("Downloaded package exceeded its signed index size.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != expected_size or digest.hexdigest() != expected_hash:
            raise ExperimentalError("Downloaded package did not match its signed SHA256/size.")
        os.replace(temporary, destination)
    except ExperimentalError:
        Path(temporary).unlink(missing_ok=True)
        raise
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        Path(temporary).unlink(missing_ok=True)
        raise ExperimentalError(f"Could not download package {filename}: {exc}") from exc
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination


def fetch_tracker(cache_file: Path, max_age: int = 6 * 60 * 60) -> dict[str, Any]:
    try:
        stat_result = cache_file.stat()
        cache_age = time.time() - stat_result.st_mtime
        if 0 <= cache_age < max_age and stat_result.st_size <= MAX_TRACKER_BYTES:
            value = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
    except (OSError, ValueError):
        pass
    payload = fetch_bytes(TRACKER_JSON_URL, MAX_TRACKER_BYTES)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ExperimentalError(f"Debian Security Tracker returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ExperimentalError("Debian Security Tracker data has an unexpected shape.")
    _write_atomic(cache_file, payload)
    return value


def package_cves(installed: dict[str, dict[str, Any]], tracker: dict[str, Any],
                 suite: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for package, installed_entry in installed.items():
        version = str(installed_entry.get("version", ""))
        tracker_package = str(installed_entry.get("source_package", package)).split()[0]
        advisories = tracker.get(tracker_package, {})
        if not isinstance(advisories, dict):
            continue
        for cve, issue in advisories.items():
            if not isinstance(issue, dict):
                continue
            release = issue.get("releases", {}).get(suite, {})
            if not isinstance(release, dict):
                continue
            status = str(release.get("status", "unknown"))
            fixed = str(release.get("fixed_version", ""))
            affected = False
            uncertain = status == "undetermined"
            if fixed:
                try:
                    affected = compare_debian_versions(version, fixed) < 0
                except ExperimentalError:
                    uncertain = True
            elif status in {"open", "vulnerable"}:
                affected = True
            if affected or uncertain:
                findings.append({
                    "package": package,
                    "version": version,
                    "cve": str(cve),
                    "status": "potentially affected" if uncertain else "affected",
                    "fixed_version": fixed,
                    "description": str(issue.get("description", "")).splitlines()[0][:240],
                })
    return sorted(findings, key=lambda item: (item["package"], item["cve"]))
