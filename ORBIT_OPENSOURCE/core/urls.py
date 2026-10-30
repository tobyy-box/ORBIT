"""URL validation helpers.

Kept separate from the launcher so both the editor (pre-save validation) and
the executor (runtime check) share one definition of a usable URL.
"""

from __future__ import annotations

from urllib.parse import urlsplit

ALLOWED_SCHEMES = {
    "http",
    "https",
    "mailto",
    "ms-settings",
    "ms-windows-store",
    "ms-screenclip",
    "steam",
    "discord",
    "spotify",
    "obsidian",
    "vscode",
    "file",
}

SCHEMELESS_SCHEMES = {"mailto", "file"}


def _looks_like_scheme(text: str) -> bool:
    head = text.split(":", 1)[0] if ":" in text else ""
    return bool(head) and all(char.isalnum() or char in "+-." for char in head)


def validate_url(url: str) -> str | None:
    """Return a human readable problem description, or ``None`` if usable."""
    text = (url or "").strip()
    if not text:
        return "Enter a website address."
    if any(char.isspace() for char in text):
        return "A website address cannot contain spaces."
    if not _looks_like_scheme(text):
        # Bare hostnames are a common mistake; ask for an explicit scheme so
        # nothing is guessed on the user's behalf.
        return "Add a scheme, for example https://example.com"
    try:
        parts = urlsplit(text)
    except ValueError:
        return "This is not a valid website address."
    scheme = parts.scheme.lower()
    if not scheme:
        return "Add a scheme, for example https://example.com"
    if scheme not in ALLOWED_SCHEMES:
        return f'"{scheme}" links are not allowed. Use https://, mailto: or a known app link.'
    if scheme in {"http", "https"} and not parts.netloc:
        return "This address is missing a domain name."
    if scheme == "mailto" and not parts.path.strip():
        return "Add the email address after mailto:"
    return None


def normalise_url(url: str) -> str:
    """Return the trimmed URL to hand to the OS."""
    return (url or "").strip()
