#!/usr/bin/env python3
"""Broker response fixture recorder and credential scrubber (Phase 1.2)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Sensitive patterns to anonymize
_SANITIZE_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r'(access[-_]?token\s*[:=]\s*["\'])([^"\']+)', re.IGNORECASE),
        r"\g<1>sanitized_access_token_demo",
    ),
    (
        re.compile(r'(jwt[-_]?token\s*[:=]\s*["\'])([^"\']+)', re.IGNORECASE),
        r"\g<1>sanitized_jwt_token_demo",
    ),
    (
        re.compile(r'(api[-_]?key\s*[:=]\s*["\'])([^"\']+)', re.IGNORECASE),
        r"\g<1>sanitized_api_key_demo",
    ),
    (
        re.compile(r'(api[-_]?secret\s*[:=]\s*["\'])([^"\']+)', re.IGNORECASE),
        r"\g<1>sanitized_api_secret_demo",
    ),
    (
        re.compile(r'(client[-_]?id\s*[:=]\s*["\'])([^"\']+)', re.IGNORECASE),
        r"\g<1>DEMO_CLIENT_123",
    ),
    (
        re.compile(r'(client[-_]?code\s*[:=]\s*["\'])([^"\']+)', re.IGNORECASE),
        r"\g<1>DEMO_CLIENT_123",
    ),
    (re.compile(r'(user[-_]?id\s*[:=]\s*["\'])([^"\']+)', re.IGNORECASE), r"\g<1>DEMO_USER_123"),
    (re.compile(r'(pan\s*[:=]\s*["\'])([A-Z]{5}\d{4}[A-Z])', re.IGNORECASE), r"\g<1>ABCDE1234F"),
    (re.compile(r"(\b\d{10}\b)"), r"9999999999"),
]


def sanitize_text(text: str) -> str:
    """Scrub sensitive broker fields using regex rules."""
    sanitized = text
    for pattern, replacement in _SANITIZE_RULES:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def sanitize_payload(data: Any) -> Any:
    """Recursively sanitize Python dict/list structures."""
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            k_lower = k.lower()
            if any(s in k_lower for s in ("token", "secret", "password")):
                out[k] = "sanitized_demo_token"
            elif any(s in k_lower for s in ("clientid", "client_id", "clientcode", "client_code")):
                out[k] = "DEMO_CLIENT_123"
            elif k_lower == "pan":
                out[k] = "ABCDE1234F"
            elif k_lower in ("email", "email_id"):
                out[k] = "demo@honba.io"
            elif k_lower in ("mobile", "phone"):
                out[k] = "9999999999"
            else:
                out[k] = sanitize_payload(v)
        return out
    if isinstance(data, list):
        return [sanitize_payload(item) for item in data]
    return data


def save_fixture(target_path: Path, data: Any) -> None:
    """Write sanitized fixture to disk in formatted JSON or raw text."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (dict, list)):
        sanitized = sanitize_payload(data)
        target_path.write_text(json.dumps(sanitized, indent=2, sort_keys=True) + "\n")
    elif isinstance(data, str):
        sanitized_text = sanitize_text(data)
        target_path.write_text(sanitized_text)
    elif isinstance(data, bytes):
        target_path.write_bytes(data)
    print(f"Fixture saved: {target_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Broker fixture recorder and sanitizer")
    parser.add_argument("--clean", type=Path, help="Clean existing fixtures in directory")
    args = parser.parse_args()

    if args.clean:
        if not args.clean.is_dir():
            print(f"Directory not found: {args.clean}", file=sys.stderr)
            sys.exit(1)
        for p in args.clean.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                save_fixture(p, data)
            except (json.JSONDecodeError, OSError) as e:
                print(f"Error cleaning {p}: {e}", file=sys.stderr)
        print("Fixtures cleaned successfully.")


if __name__ == "__main__":
    main()
