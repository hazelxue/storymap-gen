"""Stop private information from reaching a public repository.

Checks the working tree, and with --history every file in every commit plus every commit message and author line, for:
  * private identifiers (an institutional id, an organisation sub-domain, an OAuth client id, personal first names),
    which are stored here only as SHA-256 hashes so this file does not contain them;
  * e-mail addresses other than noreply and example addresses;
  * tokens and keys (GitHub, AWS, API keys, ArcGIS and OAuth tokens, private keys);
  * local file paths that name a user account;
  * commit authors and committers that are not a noreply address, and attribution trailers.

It never prints the offending text, only where it is. Exit status 1 means "do not publish".

    python scripts/privacy_check.py               working tree
    python scripts/privacy_check.py --history     working tree and all history (what the pre-push hook runs)
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("PRIVACY_CHECK_ROOT") or Path(__file__).resolve().parent.parent)

# SHA-256 of lower-cased words that must never appear (a NetID, an organisation sub-domain, an OAuth client id, two personal first names)
PRIVATE_WORD_HASHES = {
    "50fad00d60eb99dd9b673f99664d194f1855944d0ba0f9febc5a1a54ba98dded",
    "12a175edf660de93abff73d084ce8538a051fd3135c0da225f5fc60c298dc4f6",
    "af7a1a9be0a142334b30ec2a8cbe2cf20d136e1bdfd62b75fadfed18f8b10c75",
    "504aa7157b58de1cbcf0494b0cec16cded51336f658c0026956cb2f11fef49c7",
    "c1495dc48f3de433ee672638ce9d8c1c3f1ec9593c4a99411cbdc6b96c59bd49",
}
SECRET_PATTERNS = {
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    "GitHub fine-grained token": r"github_pat_[A-Za-z0-9_]{30,}",
    "AWS key": r"\bAKIA[0-9A-Z]{16}\b",
    "API key": r"\bsk-[A-Za-z0-9_-]{30,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "OAuth token in a URL": r"access_token=[A-Za-z0-9_.-]{20,}",
    "ArcGIS token": r"\b[A-Za-z0-9_-]{20,}\.\.[A-Za-z0-9_-]{40,}",
    "Windows user path": r"[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+",
    "Unix home path": r"/(?:home|Users)/[a-z][a-z0-9_.-]{2,}/",
}
EMAIL = re.compile(r"(?<![:/\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+")
ALLOWED_EMAIL = re.compile(r"(noreply|example\.(com|org|edu)|@users\.noreply\.github\.com)")
TRAILER = re.compile(r"^(co-authored-by|generated with)\b", re.I | re.M)
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".whl", ".pdf"}
MAX_BYTES = 8_000_000


def git(*args: str, binary: bool = False) -> str | bytes:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode("utf-8", errors="ignore")


def scan_text(text: str, where: str) -> list[str]:
    problems = []
    for word in set(re.findall(r"[A-Za-z0-9]{4,}", text)):
        if hashlib.sha256(word.lower().encode()).hexdigest() in PRIVATE_WORD_HASHES:
            problems.append(f"{where}: a private identifier")
    for label, pattern in SECRET_PATTERNS.items():
        if re.search(pattern, text):
            problems.append(f"{where}: {label}")
    for email in EMAIL.findall(text):
        if not ALLOWED_EMAIL.search(email):
            problems.append(f"{where}: an e-mail address ({email[:2]}***)")
    return problems


def scan_tree() -> list[str]:
    problems = []
    for name in git("ls-files").splitlines():
        path = ROOT / name
        if path.suffix.lower() in SKIP_SUFFIX or not path.is_file() or path.stat().st_size > MAX_BYTES:
            continue
        problems += scan_text(path.read_text(encoding="utf-8", errors="ignore") + " " + name, name)
    return problems


def scan_history() -> list[str]:
    problems = []
    seen: set[str] = set()
    for commit in git("rev-list", "--all").split():
        short = commit[:7]
        for line in git("ls-tree", "-r", "--long", commit).splitlines():
            meta, _, name = line.partition("\t")
            _, _, blob, size = meta.split(None, 3)
            if blob in seen or Path(name).suffix.lower() in SKIP_SUFFIX or int(size.strip() or 0) > MAX_BYTES:
                seen.add(blob)
                continue
            seen.add(blob)
            text = git("cat-file", "-p", blob, binary=True).decode("utf-8", errors="ignore")  # type: ignore[union-attr]
            problems += scan_text(text + " " + name, f"{name} (in {short})")
        header = git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", commit)
        authors = header.splitlines()[:2]
        for who in authors:
            if "noreply" not in who:
                problems.append(f"commit {short}: author or committer is not a noreply address")
        problems += scan_text(header, f"commit {short} message")
        if TRAILER.search("\n".join(header.splitlines()[2:])):
            problems.append(f"commit {short}: has an attribution trailer")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", action="store_true", help="also scan every commit")
    args = ap.parse_args()
    problems = scan_tree() + (scan_history() if args.history else [])
    if problems:
        print("privacy check FAILED; do not publish:", file=sys.stderr)
        for p in sorted(set(problems))[:50]:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"privacy check passed ({'working tree and history' if args.history else 'working tree'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
