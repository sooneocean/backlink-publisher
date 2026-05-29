#!/usr/bin/env python3
"""git-leak-check — Simple regex-based leak detection for staged changes."""

import subprocess
import sys
import re

# Patterns to look for
LEAK_PATTERNS = [
    r'client_secret\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'access_token\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'api_key\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'refresh_token\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'id_token\s*=\s*["\'][a-zA-Z0-9_\-\.]+["\']',
    r'sk-[a-zA-Z0-9]{20,}', # OpenAI keys
]

# Files whose entire diff is exempt from leak detection (test files for
# credential sanitizers necessarily contain credential-shaped strings).
_LEAK_EXEMPT_PATHS = frozenset({
    "tests/test_net_safety.py",
    "tests/test_sanitize_exception.py",
    "tests/test_config_credential_redaction.py",
})

def _is_exempt(path: str) -> bool:
    """True if the given staged-file path is fully exempt from leak detection."""
    return path in _LEAK_EXEMPT_PATHS

def get_staged_diff():
    cmd = ["git", "diff", "--cached", "--unified=0"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout

def _staged_paths() -> set[str]:
    """Return set of staged file paths (relative to repo root)."""
    cmd = ["git", "diff", "--cached", "--name-only"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return {p for p in result.stdout.splitlines() if p}

def _all_staged_are_exempt(staged: set[str]) -> bool:
    """True when every staged file is in the exempt set."""
    return bool(staged) and staged.issubset(_LEAK_EXEMPT_PATHS)

def main():
    diff = get_staged_diff()
    leaks_found = []

    # Track which file we are in (populated by "+++ b/..." lines).
    current_file = ""
    
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]  # e.g. "tests/test_net_safety.py"
            continue
        if not line.startswith('+'):
            continue
        # Strip the '+' prefix
        content = line[1:]

        # Skip lines in exempt test files.
        if _is_exempt(current_file):
            continue

        for pattern in LEAK_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                # Check if it's a known redacted value or placeholder
                if '"***"' in content or "'***'" in content:
                    continue
                leaks_found.append(line)
                break

    if leaks_found:
        print("CRITICAL: Potential secrets detected in staged changes!", file=sys.stderr)
        for leak in leaks_found:
            print(f"  {leak}", file=sys.stderr)
        print("\nPlease redact these secrets before committing.", file=sys.stderr)
        sys.exit(1)

    print("No secrets detected in staged changes.")
    sys.exit(0)

if __name__ == "__main__":
    main()
