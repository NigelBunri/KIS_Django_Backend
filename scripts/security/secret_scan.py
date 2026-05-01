#!/usr/bin/env python3
"""
Lightweight local secret scanner for KIS security validation.

It intentionally reports only file paths, line numbers, and rule names. It never
prints the matched value, so it can be used safely in logs and handoff docs.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


DEFAULT_EXCLUDES = {
    ".git",
    ".gradle",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "env",
    "ios/Pods",
    "media",
    "node_modules",
    "staticfiles",
    "uploads",
    "venv",
}

TEXT_SUFFIXES = {
    ".env",
    ".example",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}

RULES = [
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("firebase_service_account_private_key", re.compile(r'"private_key"\s*:\s*"-----BEGIN PRIVATE KEY-----')),
    ("github_token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,255}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b")),
    (
        "high_risk_secret_assignment",
        re.compile(
            r"(?i)\b("
            r"secret(_key)?|jwt_secret|internal_token|api_key|access_token|refresh_token|"
            r"password|private_key|webhook_secret|firebase_credentials_json"
            r")\b\s*[:=]\s*['\"]([^'\"\s]{16,})['\"]"
        ),
    ),
]

ALLOWLIST_PATTERNS = [
    re.compile(r"replace-with", re.IGNORECASE),
    re.compile(r"example", re.IGNORECASE),
    re.compile(r"placeholder", re.IGNORECASE),
    re.compile(r"test-internal-token", re.IGNORECASE),
    re.compile(r"testpass", re.IGNORECASE),
]


def should_skip(path: Path, roots: list[Path]) -> bool:
    if path.name == "secret_scan.py":
        return True
    parts = set(path.parts)
    if parts & DEFAULT_EXCLUDES:
        return True
    try:
        relative = next(path.relative_to(root) for root in roots if path.is_relative_to(root))
    except StopIteration:
        relative = path
    rel_text = str(relative)
    return any(segment in rel_text for segment in ("/Pods/", "/DerivedData/"))


def looks_textual(path: Path) -> bool:
    if path.name in {".env", ".env.example"}:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def is_allowlisted(line: str) -> bool:
    return any(pattern.search(line) for pattern in ALLOWLIST_PATTERNS)


def scan_file(path: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return findings
    for line_no, line in enumerate(content.splitlines(), start=1):
        if is_allowlisted(line):
            continue
        for rule_name, pattern in RULES:
            if pattern.search(line):
                findings.append((line_no, rule_name))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan KIS source trees for high-confidence secret leaks.")
    parser.add_argument("--root", action="append", default=[], help="Root directory to scan. Can be supplied multiple times.")
    parser.add_argument("--max-file-bytes", type=int, default=2_000_000)
    args = parser.parse_args()

    roots = [Path(root).expanduser().resolve() for root in args.root] or [Path.cwd()]
    all_findings: list[tuple[Path, int, str]] = []
    for root in roots:
        if not root.exists():
            print(f"SKIP missing root: {root}")
            continue
        for path in root.rglob("*"):
            if not path.is_file() or should_skip(path, roots) or not looks_textual(path):
                continue
            try:
                if path.stat().st_size > args.max_file_bytes:
                    continue
            except OSError:
                continue
            for line_no, rule_name in scan_file(path):
                all_findings.append((path, line_no, rule_name))

    for path, line_no, rule_name in all_findings:
        print(f"FINDING {path}:{line_no} {rule_name}")

    if all_findings:
        print(f"Summary: {len(all_findings)} potential secret exposure finding(s).")
        return 1
    print("Summary: no high-confidence secret exposure findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
