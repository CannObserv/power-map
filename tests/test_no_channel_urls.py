"""No live Mayfly channel URL in any tracked file (#564).

Port of upstream `tests/structural/test_no_channel_urls.py` (gregoryfoster/skills#302),
which guards only the skills repo's own tree. A channel URL, `/c/<id>#<key>`, is
read, write **and delete** access for whoever holds it, with no owner and no
revocation; this repo is public, so a committed URL is published at push. The
vendored `using-mayfly-chat` skill's Iron Law keeps it out of every durable store —
this is the part of that rule the repo can enforce on itself.

- **Match the key, not the host.** The keyless view URL a joiner's first `curl`
  returns is harmless, and a guard that fires on `mayfly.chat/c/` cries wolf.
  The pattern needs the 22-character id **and** the `#` plus 43-character key,
  on any host, since a self-hosted instance leaks the same way.
- **The pattern cannot match itself.** It is assembled from a character class,
  and `test_the_detector_is_live` builds its positive control at runtime.

`git ls-files` does not descend into submodules, so the vendored skill's own
docs are out of scope; they are upstream's to guard.
"""

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# base64url alphabet; an id is 16 bytes (22 chars), a key 32 bytes (43 chars).
_B64URL = "[A-Za-z0-9_-]"
CHANNEL_URL = re.compile("/c/" + _B64URL + "{22}" + "#" + _B64URL + "{43}")


def _tracked_files() -> list[Path]:
    """Every regular file git tracks here (submodule gitlinks excluded)."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True)
    paths = [REPO_ROOT / p for p in out.stdout.decode().split("\0") if p]
    return [p for p in paths if p.is_file() and not p.is_symlink()]


def _utf8_text(path: Path) -> str | None:
    """The file's text, or None when it is not UTF-8."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return None


class TestNoChannelUrls:
    def test_the_detector_is_live(self) -> None:
        """A positive control, built at runtime so this file never holds one."""
        live = "https://example.test/c/" + "A" * 22 + "#" + "b" * 43
        assert CHANNEL_URL.search(live), "the assembled pattern must match a live URL"
        assert not CHANNEL_URL.search("https://example.test/c/" + "A" * 22)
        assert not CHANNEL_URL.search("https://example.test/c/<ID>#<key>")

    def test_no_tracked_file_holds_a_channel_url(self) -> None:
        offenders: list[str] = []
        for path in _tracked_files():
            text = _utf8_text(path)
            if text is None:
                continue
            for match in CHANNEL_URL.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}")
        assert not offenders, (
            "A live Mayfly channel URL (id plus #key) is committed here, which "
            "publishes read, write and delete access to that channel:\n  "
            + "\n  ".join(offenders)
            + "\nRemove it, then treat the channel as leaked: stop the agents "
            "using it, delete it, and distribute a new URL privately "
            "(skills/using-mayfly-chat/references/security.md)."
        )
