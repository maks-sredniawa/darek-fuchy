#!/usr/bin/env python3
"""Signs every fucha into docs/fuchy/<id>.darek and writes docs/fuchy.json, the page Darek reads.

The private key is a PEM in the FUCHY_SIGNING_KEY environment variable. A .darek whose fucha bytes are
unchanged and whose signature still verifies is kept as it is: ECDSA signatures differ on every run,
so re-signing would rewrite every file on every push.

Usage: FUCHY_SIGNING_KEY=... python3 scripts/sign.py
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check  # noqa: E402

ROOT = check.ROOT
PUBLIC_KEY = ROOT / "keys" / "fuchy-public.pem"
OUT = ROOT / "docs"
SITE = "https://maksforge.com/darek-fuchy"
CATALOGUE_FORMAT = 1


def _openssl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["openssl", *args], capture_output=True, check=False)


def verifies(data: bytes, signature: bytes, public_key: Path = PUBLIC_KEY) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        data_path, sig_path = Path(tmp, "fucha"), Path(tmp, "sig")
        data_path.write_bytes(data)
        sig_path.write_bytes(signature)
        result = _openssl("dgst", "-sha256", "-verify", str(public_key), "-signature", str(sig_path), str(data_path))
        return result.returncode == 0 and result.stdout.strip() == b"Verified OK"


def sign(data: bytes, key_path: Path) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        data_path, sig_path = Path(tmp, "fucha"), Path(tmp, "sig")
        data_path.write_bytes(data)
        result = _openssl("dgst", "-sha256", "-sign", str(key_path), "-out", str(sig_path), str(data_path))
        if result.returncode != 0:
            raise SystemExit(f"openssl could not sign: {result.stderr.decode(errors='replace').strip()}")
        return sig_path.read_bytes()


def envelope(data: bytes, signature: bytes) -> bytes:
    body = {"fucha": base64.b64encode(data).decode(), "signature": base64.b64encode(signature).decode()}
    return (json.dumps(body, indent=2) + "\n").encode()


def still_signed(path: Path, data: bytes) -> bool:
    try:
        old = json.loads(path.read_bytes())
        return base64.b64decode(old["fucha"], validate=True) == data and verifies(
            data, base64.b64decode(old["signature"], validate=True)
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def entry(source: dict) -> dict:
    return {
        "id": source["id"],
        "version": source["version"],
        "name": source["name"],
        "description": source["description"],
        "author": source["author"],
        "profanity": source["profanity"],
        "actions": list(dict.fromkeys(c["action"] for c in source["commands"])),
        "url": f"{SITE}/fuchy/{source['id']}.darek",
    }


def main() -> int:
    pem = os.environ.get("FUCHY_SIGNING_KEY", "")
    if not pem.strip():
        print("FUCHY_SIGNING_KEY is empty", file=sys.stderr)
        return 1
    (OUT / "fuchy").mkdir(parents=True, exist_ok=True)
    entries, signed, kept = [], [], []
    with tempfile.TemporaryDirectory() as tmp:
        key_path = Path(tmp, "key.pem")
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        for path in check.fucha_files():
            data = path.read_bytes()
            source, problems = check.parse(data)
            if problems:
                print(f"{path}: {problems[0]}", file=sys.stderr)
                return 1
            target = OUT / "fuchy" / f"{source['id']}.darek"
            if still_signed(target, data):
                kept.append(source["id"])
            else:
                signature = sign(data, key_path)
                if not verifies(data, signature):
                    print(f"{source['id']}: the new signature does not verify against {PUBLIC_KEY.name}", file=sys.stderr)
                    return 1
                target.write_bytes(envelope(data, signature))
                signed.append(source["id"])
            entries.append(entry(source))
    ids = {e["id"] for e in entries}
    removed = [p.stem for p in (OUT / "fuchy").glob("*.darek") if p.stem not in ids]
    for stem in removed:
        (OUT / "fuchy" / f"{stem}.darek").unlink()
    catalogue = {"format": CATALOGUE_FORMAT, "fuchy": entries}
    (OUT / "fuchy.json").write_text(json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"signed: {signed or '-'}; unchanged: {kept or '-'}; removed: {removed or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
