"""Each third-party apt origin the knob follows is scoped by apt pins (#658).

``.skills/patching-hosts`` follows Tailscale, NodeSource and the GitHub CLI in
the maintenance lane. Named by its site, a followed origin selects every
upgradable package that site serves, and at apt's default priority, 500, which
ties Ubuntu's, a package it serves under an Ubuntu name at a higher version
replaces Ubuntu's (the skill's policy.md, § Two lanes). The probe reports that
as ``unscoped:<site>`` and ``unpinned:<package>``. The pins that scope each one:

- **a ``Package: *`` pin for its site, below 500 and at least 100**, so Ubuntu's
  version stays for everything else, and nothing from the site is installed
  unasked;
- **a package pin above 500 for each package it was added for**, so the lane
  keeps taking those.

The test reads the knob, so a followed origin added there without its pins reds
here: the seam between the knob and ``infra/``. Then apt parses the file itself,
since a stanza apt rejects is an error on every later apt run on the host.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Reads the .skills/ knob: runs on a skills-vendor/ bump's commit too (#751).
pytestmark = pytest.mark.vendored

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "infra" / "apt-preferences.d" / "90-power-map-origins.pref"
KNOB = ROOT / ".skills" / "patching-hosts"

# What each followed origin was added for: the packages it may upgrade.
ADDED_FOR = {
    "pkgs.tailscale.com": {"tailscale", "tailscale-archive-keyring"},
    "deb.nodesource.com": {"nodejs"},
    "cli.github.com": {"gh"},
}

# apt reads a preferences.d file only if its name is these characters and it
# has no extension or `.pref` (apt_preferences(5)).
APT_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def _stanzas() -> list[dict[str, str]]:
    """The file's stanzas, each a field map; comment lines are dropped."""
    stanzas = []
    for block in re.split(r"\n\s*\n", PINS.read_text(encoding="utf-8")):
        fields = {}
        for line in block.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
        if fields:
            stanzas.append(fields)
    return stanzas


def _followed_origins() -> set[str]:
    """Each site the knob declares ``origin <site> follow`` for."""
    origins = set()
    for raw in KNOB.read_text(encoding="utf-8").splitlines():
        words = []
        for word in raw.split():
            if word.startswith("#"):
                break
            words.append(word)
        if len(words) >= 3 and words[0] == "origin" and words[2] == "follow":
            origins.add(words[1])
    return origins


def _pins_for(site: str) -> list[tuple[str, int]]:
    """``(Package value, priority)`` of each stanza pinned to ``site``."""
    return [
        (stanza["Package"], int(stanza["Pin-Priority"]))
        for stanza in _stanzas()
        if stanza.get("Pin") == f"origin {site}"
    ]


def test_the_file_is_one_apt_reads() -> None:
    assert PINS.is_file(), f"missing {PINS.relative_to(ROOT)}"
    assert APT_NAME.fullmatch(PINS.name)
    assert PINS.suffix == ".pref", "apt ignores a preferences.d file with another extension"


def test_every_stanza_is_complete() -> None:
    stanzas = _stanzas()
    assert stanzas
    for stanza in stanzas:
        assert set(stanza) == {"Package", "Pin", "Pin-Priority"}, stanza
        assert stanza["Pin"].startswith("origin "), stanza


def test_the_knob_and_the_pins_name_the_same_origins() -> None:
    followed = _followed_origins()
    assert followed, "the knob follows no origin: the parse is broken"
    pinned = {stanza["Pin"].removeprefix("origin ") for stanza in _stanzas()}
    assert pinned == followed == set(ADDED_FOR)


@pytest.mark.parametrize("site", sorted(ADDED_FOR))
def test_each_followed_origin_is_scoped_below_ubuntu(site: str) -> None:
    catch_all = [prio for package, prio in _pins_for(site) if package == "*"]
    assert len(catch_all) == 1, f"{site}: one Package: * pin"
    assert 100 <= catch_all[0] < 500, f"{site}: catch-all at {catch_all[0]}"


@pytest.mark.parametrize("site", sorted(ADDED_FOR))
def test_what_each_origin_was_added_for_is_pinned_above_ubuntu(site: str) -> None:
    pinned = {}
    for package, prio in _pins_for(site):
        if package != "*":
            for name in package.split():
                pinned[name] = prio
    assert set(pinned) == ADDED_FOR[site]
    for name, prio in pinned.items():
        assert prio > 500, f"{site}: {name} at {prio}"


@pytest.mark.skipif(shutil.which("apt-cache") is None, reason="apt-cache not installed")
def test_apt_parses_the_file(tmp_path: Path) -> None:
    """apt reads exactly this file, and nothing else from the host's preferences."""
    parts = tmp_path / "preferences.d"
    parts.mkdir()
    shutil.copy(PINS, parts / PINS.name)
    result = subprocess.run(
        [
            "apt-cache",
            "-o",
            "Dir::Etc::Preferences=/dev/null",
            "-o",
            f"Dir::Etc::PreferencesParts={parts}",
            "policy",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not re.search(r"^(E|W):", result.stderr, re.MULTILINE), result.stderr
