#!/usr/bin/env python3
"""Generate profile.svg / profile-light.svg — a neofetch-style GitHub profile card.

Layout: MyOrigines logo rendered as ASCII art on top, then a neofetch-like info
block with dotted leaders, then live GitHub stats (GraphQL + REST).

Env:
  PROFILE_LOGIN  GitHub login to render (default: Math-MO)
  ACCESS_TOKEN   PAT with `repo` scope (sees private repos) — preferred
  GITHUB_TOKEN   fallback (public repos only)
If the API is unreachable the last cached stats (cache/stats.json) are reused so
the SVG is always produced.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
LOGIN = os.environ.get("PROFILE_LOGIN", "Math-MO")
TOKEN = os.environ.get("ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")

BIRTHDAY = date(2002, 1, 19)
JOINED_MYO = date(2026, 6, 8)

INFO_WIDTH = 92
FONT_SIZE = 12
LINE_H = 15
CHAR_W = 7.3
PAD = 20
# The logo uses a smaller font => more cells across the same card width.
LOGO_FONT_SIZE = 10
LOGO_LINE_H = 11
LOGO_CHAR_W = 6.1
LOGO_COLS = int(INFO_WIDTH * CHAR_W / LOGO_CHAR_W)

# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested in tests/test_generate.py)
# --------------------------------------------------------------------------- #

def add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def human_duration(start: date, end: date) -> str:
    """'24 years, 7 months, 1 day' — calendar-accurate, zero parts dropped."""
    months = (end.year - start.year) * 12 + end.month - start.month
    if add_months(start, months) > end:
        months -= 1
    days = (end - add_months(start, months)).days
    years, months = divmod(months, 12)
    parts = []
    if years:
        parts.append(_plural(years, "year"))
    if months:
        parts.append(_plural(months, "month"))
    if days or not parts:
        parts.append(_plural(days, "day"))
    return ", ".join(parts)


def leader(label: str, value: str, width: int, prefix: str = "- ") -> tuple[str, str, str]:
    """Return (label, dots, value) whose lengths sum to `width` when possible."""
    label_s = f"{prefix}{label}: "
    n = width - len(label_s) - len(str(value))
    dots = "." * (n - 1) + " " if n >= 2 else " "
    return label_s, dots, str(value)


def fmt_int(n: int) -> str:
    return f"{n:,}"


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# Commits are authored under several identities (work Mac, Claude Code sessions,
# a second GitHub account...). Everything matching below counts as "me".
MY_LOGINS = {"math-mo", "pooxie"}
MY_EMAILS = re.compile(
    r"^(devclaude\d*@divabox\.net"
    r"|mathmyo@.+"
    r"|m\.riba@divabox\.net"
    r"|matheo\.riba2a@gmail\.com"
    r"|\d+\+math-mo@users\.noreply\.github\.com)$",
    re.IGNORECASE,
)


def is_me(login: str | None, email: str | None) -> bool:
    if login and login.lower() in MY_LOGINS:
        return True
    return bool(email and MY_EMAILS.match(email.strip()))


# --------------------------------------------------------------------------- #
# Logo -> half-block pixel art. Each character cell holds two vertical pixels
# (▀ top, ▄ bottom, █ both); a cell whose halves differ in colour is drawn as
# two overlapping <text> layers at the same baseline.
# Classes: 'ring' = the beige O, 'mark' = the black lettering.
# --------------------------------------------------------------------------- #

Segment = tuple[str, str]          # (text, css class)
Line = list[Segment]

THRESHOLD = {"mark": 0.40, "ring": 0.35}   # coverage needed to light a pixel, per colour


def render_logo(path: Path, cols: int = LOGO_COLS, char_aspect: float = 0.5) -> list[list[Line]]:
    """Return rows; each row is 1 or 2 layers (Lines) to draw at the same y."""
    from PIL import Image, ImageOps

    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(bg, im).convert("RGB")
    gray = rgb.convert("L")
    bbox = ImageOps.invert(gray).point(lambda p: 255 if p > 20 else 0).getbbox()
    rgb = rgb.crop(bbox)
    w, h = rgb.size
    rows = max(1, int(h / w * cols * char_aspect)) * 2          # 2 pixels per cell

    # Split the source into two coverage masks *before* downsampling, so a cell
    # where the beige ring runs behind a black letter keeps the letter.
    mark_mask = Image.new("L", rgb.size)
    ring_mask = Image.new("L", rgb.size)
    mark_px, ring_px = mark_mask.load(), ring_mask.load()
    src = rgb.load()
    for yy in range(h):
        for xx in range(w):
            r, g, b = src[xx, yy]
            lum = (r * 299 + g * 587 + b * 114) // 1000
            if lum < 110:
                mark_px[xx, yy] = 255
            elif r - b > 25:
                ring_px[xx, yy] = 255
    mark_cov = mark_mask.resize((cols, rows), Image.BOX).load()
    ring_cov = ring_mask.resize((cols, rows), Image.BOX).load()

    def pixel(x: int, y: int) -> str | None:
        if mark_cov[x, y] / 255.0 > THRESHOLD["mark"]:
            return "mark"
        if ring_cov[x, y] / 255.0 > THRESHOLD["ring"]:
            return "ring"
        return None

    out: list[list[Line]] = []
    for y in range(0, rows, 2):
        layer1: list[tuple[str, str | None]] = []
        layer2: list[tuple[str, str | None]] = []
        for x in range(cols):
            t, b = pixel(x, y), pixel(x, y + 1)
            if t and b and t == b:
                layer1.append(("█", t)); layer2.append((" ", None))
            elif t and b:
                layer1.append(("▀", t)); layer2.append(("▄", b))
            elif t:
                layer1.append(("▀", t)); layer2.append((" ", None))
            elif b:
                layer1.append(("▄", b)); layer2.append((" ", None))
            else:
                layer1.append((" ", None)); layer2.append((" ", None))
        out.append([_merge_runs(layer) for layer in (layer1, layer2) if any(c != " " for c, _ in layer)])
    return out


def _merge_runs(cells: list[tuple[str, str | None]]) -> Line:
    """Collapse consecutive cells of the same class into one segment (blank cells -> 'dots')."""
    line: Line = []
    for ch, cls in cells:
        cls = cls or "dots"
        if line and line[-1][1] == cls:
            line[-1] = (line[-1][0] + ch, cls)
        else:
            line.append((ch, cls))
    while line and line[-1][0].strip() == "":
        line.pop()
    return line


# --------------------------------------------------------------------------- #
# GitHub stats
# --------------------------------------------------------------------------- #

def _request(url: str, data: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(url, method="POST" if data else "GET")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", f"{LOGIN}-profile-generator")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    body = json.dumps(data).encode() if data else None
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, body, timeout=60) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def graphql(query: str, variables: dict) -> dict:
    status, payload = _request("https://api.github.com/graphql", {"query": query, "variables": variables})
    if status != 200 or not payload or "errors" in payload:
        raise RuntimeError(f"GraphQL {status}: {payload}")
    return payload["data"]


USER_QUERY = """
query($login: String!, $after: String) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    repositoriesContributedTo(first: 1, includeUserRepositories: false,
      contributionTypes: [COMMIT, PULL_REQUEST, ISSUE, PULL_REQUEST_REVIEW]) { totalCount }
    owned: repositories(first: 1, ownerAffiliations: [OWNER]) { totalCount }
    repositories(first: 100, after: $after, ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER],
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes { nameWithOwner stargazerCount pushedAt isFork owner { login } }
    }
  }
}
"""

def fetch_user() -> dict:
    repos, after, user = [], None, None
    while True:
        data = graphql_retry(USER_QUERY, {"login": LOGIN, "after": after})["user"]
        user = user or data
        page = data["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    user["all_repos"] = repos
    return user


HISTORY_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target { ... on Commit {
        history(first: 100, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes { additions deletions author { email user { login } } }
        }
      } }
    }
  }
}
"""


def graphql_retry(query: str, variables: dict, tries: int = 4) -> dict:
    for attempt in range(tries):
        try:
            return graphql(query, variables)
        except RuntimeError as e:
            if attempt == tries - 1 or "GraphQL 502" not in str(e) and "GraphQL 5" not in str(e):
                raise
            time.sleep(3 * (attempt + 1))
    raise AssertionError("unreachable")


def repo_history(nwo: str) -> dict:
    """{'commits', 'add', 'del'} authored by me on the default branch."""
    owner, name = nwo.split("/", 1)
    commits = add = dele = 0
    after = None
    while True:
        data = graphql_retry(HISTORY_QUERY, {"owner": owner, "name": name, "after": after})
        ref = data["repository"]["defaultBranchRef"]
        if not ref:                      # empty repository
            break
        hist = ref["target"]["history"]
        for c in hist["nodes"]:
            a = c["author"] or {}
            if is_me((a.get("user") or {}).get("login"), a.get("email")):
                commits += 1
                add += c["additions"]
                dele += c["deletions"]
        if not hist["pageInfo"]["hasNextPage"]:
            break
        after = hist["pageInfo"]["endCursor"]
    return {"commits": commits, "add": add, "del": dele}


def collect_stats() -> dict:
    CACHE.mkdir(exist_ok=True)
    stats_file, loc_file = CACHE / "stats.json", CACHE / "loc.json"
    cached = json.loads(stats_file.read_text()) if stats_file.exists() else None
    if cached and not os.environ.get("ACCESS_TOKEN"):
        # The default GITHUB_TOKEN only sees public repos: refreshing with it would
        # silently shrink every number. Keep the last full snapshot instead.
        print("[warn] ACCESS_TOKEN not set; reusing cached stats", file=sys.stderr)
        return cached
    try:
        user = fetch_user()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] GitHub API unavailable ({e}); reusing cached stats", file=sys.stderr)
        if cached:
            return cached
        raise

    loc_cache = json.loads(loc_file.read_text()) if loc_file.exists() else {}
    commits = add = dele = 0
    for repo in user["all_repos"]:
        nwo = repo["nameWithOwner"]
        entry = loc_cache.get(nwo)
        if not entry or entry.get("pushedAt") != repo["pushedAt"]:
            try:
                entry = {"pushedAt": repo["pushedAt"], **repo_history(nwo)}
            except Exception as e:  # noqa: BLE001
                print(f"[warn] history unavailable for {nwo}: {e}", file=sys.stderr)
                entry = entry or {"pushedAt": None, "commits": 0, "add": 0, "del": 0}
            loc_cache[nwo] = entry
        commits += entry["commits"]
        add += entry["add"]
        dele += entry["del"]
    loc_file.write_text(json.dumps(loc_cache, indent=2, sort_keys=True) + "\n")

    stats = {
        "repos": user["owned"]["totalCount"],
        "contributed": user["repositoriesContributedTo"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in user["all_repos"] if r["owner"]["login"].lower() == LOGIN.lower()),
        "followers": user["followers"]["totalCount"],
        "commits": commits,
        "loc_add": add,
        "loc_del": dele,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    stats_file.write_text(json.dumps(stats, indent=2) + "\n")
    return stats


# --------------------------------------------------------------------------- #
# Info block
# --------------------------------------------------------------------------- #

def info_lines(stats: dict, today: date) -> list[Line]:
    def row(label: str, value: str, width: int = INFO_WIDTH) -> Line:
        lab, dots, val = leader(label, value, width)
        return [("- ", "text"), (lab[2:], "label"), (dots, "dots"), (val, "text")]

    def pair(l1: str, v1: str, l2: str, v2: str) -> Line:
        left = row(l1, v1, 48)
        lab, dots, val = leader(l2, v2, INFO_WIDTH - 48 - 3, prefix="")
        return left + [(" | ", "dots"), (lab, "label"), (dots, "dots"), (val, "text")]

    def section(title: str) -> Line:
        return [("- ", "text"), (title, "label")]

    head = [("math", "label"), ("@", "text"), ("myorigines", "label")]
    tenure = human_duration(JOINED_MYO, today)
    lines: list[Line] = [
        head,
        [("-" * 15, "dots")],
        row("OS", "macOS 26 Tahoe, iOS, Linux"),
        row("Uptime", human_duration(BIRTHDAY, today)),
        row("Host", "MyOrigines"),
        row("Uptime.MyOrigines", f"{tenure} (coffee: mastered, codebase: loading...)"),
        row("Kernel", "AI Developer"),
        row("IDE", "Claude Code, Xcode, VS Code"),
        [],
        row("Languages.Programming", "TypeScript, Swift, Python, Rust, C#"),
        row("Languages.Computer", "HTML, CSS, JSON, YAML, SQL"),
        row("Languages.Real", "Français, English"),
        [],
        row("Hobbies.Software", "Generative music, native macOS apps"),
        [],
        section("Contact"),
        row("Email.Personal", "matheo.riba2a@gmail.com"),
        row("Email.Work", "m.riba@divabox.net"),
        row("GitHub", f"github.com/{LOGIN}"),
        [],
        section("GitHub Stats"),
        pair("Repos", f"{fmt_int(stats['repos'])} {{Contributed: {fmt_int(stats['contributed'])}}}",
             "Stars", fmt_int(stats["stars"])),
        pair("Commits", fmt_int(stats["commits"]), "Followers", fmt_int(stats["followers"])),
    ]
    loc_total = stats["loc_add"] - stats["loc_del"]
    lab, dots, val = leader("Lines of Code on GitHub", fmt_int(loc_total), 56)
    lines.append([
        ("- ", "text"), (lab[2:], "label"), (dots, "dots"), (val, "text"),
        (" ( ", "dots"), (f"{fmt_int(stats['loc_add'])}++", "green"), (", ", "dots"),
        (f"{fmt_int(stats['loc_del'])}--", "red"), (" )", "dots"),
    ])
    lines.append([])
    lines.append([(f"updated {stats['updated']}", "dots")])
    return lines


# --------------------------------------------------------------------------- #
# SVG
# --------------------------------------------------------------------------- #

THEMES = {
    "dark": dict(bg="#0d1117", text="#c9d1d9", label="#e3b587", dots="#484f58",
                 ring="#d4b5a0", mark="#f0f6fc", green="#3fb950", red="#f85149"),
    "light": dict(bg="#ffffff", text="#24292f", label="#9a6700", dots="#afb8c1",
                  ring="#c9a58c", mark="#1f2328", green="#1a7f37", red="#cf222e"),
}


def build_svg(logo: list[list[Line]], info: list[Line], theme: dict) -> str:
    n_cols = max([sum(len(t) for t, _ in ln) for ln in info] + [INFO_WIDTH])
    width = int(n_cols * CHAR_W) + 2 * PAD
    height = len(logo) * LOGO_LINE_H + LINE_H + len(info) * LINE_H + 2 * PAD

    css = "\n".join(f".{k} {{ fill: {v}; }}" for k, v in theme.items() if k != "bg")
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="\'JetBrains Mono\',\'Fira Code\',SFMono-Regular,'
        f'Menlo,Consolas,\'Liberation Mono\',monospace" font-size="{FONT_SIZE}" xml:space="preserve">',
        f"<style>{css}\n.label {{ font-weight: 600; }}</style>",
        f'<rect width="100%" height="100%" rx="8" fill="{theme["bg"]}"/>',
    ]

    def text(ln: Line, y: int, size: int = FONT_SIZE) -> str:
        # NBSP: leading/multiple spaces survive every renderer (xml:space is not universally honoured)
        spans = "".join(f'<tspan class="{cls}">{esc(t).replace(" ", "&#160;")}</tspan>' for t, cls in ln if t)
        attr = f' font-size="{size}"' if size != FONT_SIZE else ""
        return f'<text x="{PAD}" y="{y}"{attr}>{spans}</text>'

    y = PAD + LOGO_FONT_SIZE
    for layers in logo:
        out.extend(text(layer, y, LOGO_FONT_SIZE) for layer in layers)
        y += LOGO_LINE_H
    y += LINE_H
    for ln in info:
        if ln:
            out.append(text(ln, y))
        y += LINE_H
    out.append("</svg>\n")
    return "\n".join(out)


def main() -> None:
    today = date.today()
    logo = render_logo(ROOT / "assets" / "logo.png")

    stats = collect_stats()
    info = info_lines(stats, today)
    (ROOT / "profile.svg").write_text(build_svg(logo, info, THEMES["dark"]), encoding="utf-8")
    (ROOT / "profile-light.svg").write_text(build_svg(logo, info, THEMES["light"]), encoding="utf-8")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
