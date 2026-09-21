#!/usr/bin/env python3
"""Generate modules.hackyguru.com — static catalog page for this repo.

Reads the catalog's index.json (as published in the `index` release) and
logos-repo.json, and emits a single self-contained index.html. Run by the
deploy-site workflow after every index rebuild; runnable locally too:

    curl -fsSL $(python3 -c "import json;print(json.load(open('logos-repo.json'))['indexUrl'])") -o /tmp/index.json
    python3 site/gen_site.py --index /tmp/index.json --out _site

Styling mirrors hackyguru.com — #121212 background, Inter body, mono
headings with `//` markers, translucent white/5 cards with white/10 borders.
"""
import argparse, base64, glob, hashlib, html, json, os, re, shutil

# Directory holding this script and the static assets it publishes.
HERE = os.path.dirname(os.path.abspath(__file__))

REPO_GH = "https://github.com/hackyguru/logos-modules"
SOURCE_GH = "https://github.com/hackyguru/logos-workshop"

_GH_RELEASE = re.compile(r"^(https://github\.com/[^/]+/[^/]+)/releases/download/", re.I)

# "Basecamp Tutorials" playlist, in playlist order. Edit this list to add or
# reorder episodes — the modal's sidebar and player are generated from it, and
# the "Part N" labels come from the position here, not from the video titles.
PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLKLihqaY2Vc0"
TUTORIALS = [
    ("Logos Basecamp: Quickstart", "EwCkegIm_1o"),
    ("Installing Logos Basecamp", "SZ72xolkZz4"),
    ("Understanding .lgx modules on Logos Basecamp", "yZ_93uAAY9A"),
    ("Building your first hello-world UI module on Logos Basecamp", "RPIWZ1RnYfA"),
]


def app_base(name: str) -> str:
    """The app a module belongs to: its name minus a `_ui` or `_core` suffix.
    Pairs both `<x>`+`<x>_ui` (e.g. filesharing) and `<x>_core`+`<x>` (Persona:
    persona_core + persona)."""
    for suf in ("_ui", "_core"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def prettify(name: str) -> str:
    return " ".join(w.capitalize() for w in app_base(name).split("_"))


def latest(pkg: dict) -> dict:
    return max(pkg["versions"], key=lambda v: v.get("releasedAt", ""))


def platforms(manifest: dict) -> list:
    plats = sorted(k.split("/", 1)[1] for k in manifest.get("hashes", {}) if k.startswith("variants/"))
    return plats or sorted(manifest.get("main", {}).keys())


def source_icon(module_name: str):
    """The icon a module's own metadata.json names, resolved in its source.

    Newer module-builder releases rewrite the manifest's `icon` to a
    generated `assets/icon.png` that exists only inside the .lgx, so the
    manifest name alone no longer finds the file in the checkout."""
    if not module_name:
        return None
    for meta in glob.glob("submodules/**/metadata.json", recursive=True):
        try:
            m = json.load(open(meta))
        except (OSError, ValueError):
            continue
        if m.get("name") == module_name and m.get("icon"):
            p = os.path.join(os.path.dirname(meta), m["icon"])
            if os.path.isfile(p):
                return p
    return None


def find_icon(icon_name: str, module_name: str = ""):
    """Base64 data-URI for a module icon.

    The module's own metadata.json is tried first, then the manifest's icon
    name in the checked-out submodules, then site/icons/ — externally
    published modules (external-modules.txt) have no submodule here, so
    their icon is vendored under site/icons/ instead."""
    if not icon_name:
        return None
    base = os.path.basename(icon_name)
    exact = source_icon(module_name)
    candidates = ([exact] if exact else []) \
        + glob.glob(f"submodules/**/{base}", recursive=True) \
        + glob.glob(f"site/icons/{base}")
    for p in candidates:
        try:
            b = open(p, "rb").read()
        except OSError:
            continue
        if len(b) > 300_000:
            continue
        mime = "image/svg+xml" if p.endswith(".svg") else "image/png"
        return f"data:{mime};base64," + base64.b64encode(b).decode()
    return None


def source_link(vers: list) -> str:
    """Repo to credit on a card.

    Locally-built packages are released from the catalog repo itself and
    their source lives in the workshop monorepo, so they get SOURCE_GH.
    Externally published packages (external-modules.txt) are released
    from the repo that HOLDS their source — link that one instead."""
    for v in vers:
        m = _GH_RELEASE.match(v.get("url", "") or "")
        if m and m.group(1).rstrip("/").lower() != REPO_GH.lower():
            return m.group(1)
    return SOURCE_GH


def render_tutorials() -> str:
    """Sidebar entries for the tutorial modal — one button per episode.

    The first is marked current so the modal has a selection before any
    click; the player's iframe src is set by JS on open, not here, so no
    YouTube request happens until the user actually asks for one."""
    return "\n".join(
        f'        <button type="button" class="tut-item{" current" if i == 1 else ""}" '
        f'data-id="{html.escape(vid)}" data-n="{i}" '
        f'aria-current="{"true" if i == 1 else "false"}">'
        f'<span class="tut-n mono">Part {i}</span>'
        f'<span class="tut-t">{html.escape(name)}</span></button>'
        for i, (name, vid) in enumerate(TUTORIALS, 1)
    )


def group_apps(packages: list) -> list:
    """Merge a core module and its UI into one app card. Groups by app base
    name (see `app_base`), so both `<x>`+`<x>_ui` and `<x>_core`+`<x>` pair up;
    a lone module gets its own card. First-appearance order is preserved."""
    groups, order = {}, []
    for pkg in packages:
        base = app_base(pkg["name"])
        if base not in groups:
            groups[base] = []
            order.append(base)
        groups[base].append(pkg)
    return [groups[base] for base in order]


def render_app(members: list) -> str:
    vers = [latest(p) for p in members]
    manifests = [v["manifest"] for v in vers]
    ui = next((m for m in manifests if m.get("type") == "ui_qml"), None)
    core = next((m for m in manifests if m.get("type") != "ui_qml"), manifests[0])
    title = prettify(core["name"])
    desc = (ui or core).get("description", "")
    category = core.get("category", "")
    version = core.get("version", "")
    released = max(v.get("releasedAt", "") for v in vers)[:10]
    size = sum(v.get("size", 0) for v in vers)
    size_h = f"{size/1e6:.1f} MB" if size > 1e6 else f"{size/1e3:.0f} KB"
    own = {m["name"] for m in manifests}
    deps = sorted({d for m in manifests for d in m.get("dependencies", []) if d not in own})
    plats = sorted({p for m in manifests for p in platforms(m)})
    signed = all(v.get("signature") for v in vers)
    icon = next((i for i in (find_icon(m.get("icon"), m.get("name", "")) for m in manifests if m.get("icon")) if i), None)

    icon_html = (
        f'<img class="icon" src="{icon}" alt="">' if icon
        else f'<div class="icon icon-fallback mono">{html.escape(title[0])}</div>'
    )
    deps_html = (
        '<div class="row"><span class="label mono">requires</span>'
        + "".join(f'<span class="chip mono">{html.escape(d)}</span>' for d in deps)
        + "</div>"
    ) if deps else ""
    plats_html = "".join(f'<span class="plat mono">{html.escape(p)}</span>' for p in plats)

    dl_html = "".join(
        f'<a class="btn btn-sm" href="{html.escape(v["url"])}" title="{html.escape(os.path.basename(v["url"]))}">'
        f'download{"" if len(vers) == 1 else (" ui" if m.get("type") == "ui_qml" else " core")} ⤓</a>'
        for m, v in zip(manifests, vers) if v.get("url")
    )

    return f"""
    <article class="card">
      <div class="card-head">
        {icon_html}
        <div>
          <h3>{html.escape(title)} <span class="ver mono">v{html.escape(version)}</span></h3>
          <div class="meta mono">{html.escape(category)} · {size_h} · {released}{' · signed' if signed else ''}</div>
        </div>
      </div>
      <p class="desc">{html.escape(desc)}</p>
      {deps_html}
      <div class="card-foot">
        <div class="plats">{plats_html}</div>
        <div class="actions">
          <a class="src mono" href="{source_link(vers)}" target="_blank" rel="noopener">source ↗</a>
          {dl_html}
        </div>
      </div>
    </article>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="index.json")
    ap.add_argument("--repo", default="logos-repo.json")
    ap.add_argument("--out", default="_site")
    args = ap.parse_args()

    repo = json.load(open(args.repo))
    try:
        index = json.load(open(args.index))
    except (OSError, json.JSONDecodeError):
        index = {"packages": []}

    apps = group_apps(index.get("packages", []))
    cards = "\n".join(render_app(m) for m in apps) if apps else (
        '<p class="empty mono">no modules published yet — check back soon.</p>'
    )
    tut_items = render_tutorials()
    repo_url = f"{repo['homepage']}/logos-repo.json"
    generated = html.escape(index.get("generatedAt", ""))

    site_url = repo["homepage"]
    title = repo.get("displayName", "Guru's Logos Modules")
    description = repo["description"]
    # Social card for this site specifically, in the same theme as the page
    # (site/og.png). 1200x630 is the 1.91:1 ratio X and Facebook crop toward,
    # so nothing important gets clipped. favicon.ico is already the same file
    # as hackyguru.com's (identical sha256).
    #
    # Published under a content-hashed name. X (and Facebook, LinkedIn, …)
    # cache the IMAGE by its URL on their own CDNs, separately from the page
    # scrape — so replacing og.png in place leaves them serving the previous
    # card even after they re-read the page. A hashed filename means a new
    # card is always a new URL, which they have no choice but to fetch.
    # og.png stays published too, for anyone linking it directly.
    og_hash = hashlib.sha256(open(os.path.join(HERE, "og.png"), "rb").read()).hexdigest()[:10]
    og_name = f"og.{og_hash}.png"
    og_image = f"{site_url}/{og_name}"
    site_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": title,
        "url": site_url,
        "description": description,
        "author": {
            "@type": "Person",
            "name": "Kumaraguru",
            "alternateName": "Guru",
            "url": "https://hackyguru.com",
        },
    })

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<meta name="author" content="Kumaraguru">
<meta name="robots" content="index, follow">
<meta name="theme-color" content="#121212">
<link rel="icon" href="favicon.ico" sizes="any">
<link rel="canonical" href="{site_url}">

<!-- Open Graph -->
<meta property="og:type" content="website">
<meta property="og:site_name" content="{html.escape(title)}">
<meta property="og:title" content="{html.escape(title)}">
<meta property="og:description" content="{html.escape(description)}">
<meta property="og:url" content="{site_url}">
<meta property="og:image" content="{og_image}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:type" content="image/png">
<meta property="og:image:alt" content="{html.escape(title)}">
<meta name="twitter:image:alt" content="{html.escape(title)}">
<meta property="og:locale" content="en_US">

<!-- Twitter -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@hackyguru">
<meta name="twitter:creator" content="@hackyguru">
<meta property="twitter:domain" content="modules.hackyguru.com">
<meta property="twitter:url" content="{site_url}">
<meta name="twitter:title" content="{html.escape(title)}">
<meta name="twitter:description" content="{html.escape(description)}">
<meta name="twitter:image" content="{og_image}">

<script type="application/ld+json">{site_ld}</script>
<link href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #121212;
    --fg: #f4f4f5;            /* zinc-100 */
    --text: #a1a1aa;          /* zinc-400 */
    --dim: #9ca3af;           /* gray-400 */
    --dimmer: #71717a;        /* zinc-500 */
    --card: rgba(255, 255, 255, 0.05);
    --card-hover: rgba(255, 255, 255, 0.10);
    --border: rgba(255, 255, 255, 0.10);
    --border-hover: rgba(255, 255, 255, 0.20);
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: "Inter", -apple-system, "Segoe UI", sans-serif;
    font-size: 16px; line-height: 1.6;
    text-rendering: optimizeLegibility;
    -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
  }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  ::selection {{ background: rgba(255,255,255,.2); }}
  main {{ max-width: 880px; margin: 0 auto; padding: 112px 24px 96px; }}

  /* title bar — same as hackyguru.com's navbar */
  .topbar {{
    position: fixed; top: 0; left: 0; width: 100%; z-index: 50;
    border-bottom: 1px solid transparent;
    transition: background .3s ease, border-color .3s ease, backdrop-filter .3s ease;
  }}
  .topbar.scrolled {{
    background: rgba(18, 18, 18, .8);
    backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    border-bottom-color: rgba(255, 255, 255, .1);
  }}
  .topbar nav {{
    display: flex; align-items: center; justify-content: space-between;
    height: 64px; padding: 0 16px;
  }}
  @media (min-width: 768px) {{ .topbar nav {{ padding: 0 32px; }} }}
  .topbar .avatar img {{ width: 40px; height: 40px; display: block; object-fit: cover; }}
  .topbar .links {{
    display: none; align-items: center; gap: 32px;
    border: 1px solid var(--border); border-radius: 999px;
    background: var(--card); padding: 8px 24px;
    backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
    box-shadow: 0 10px 15px -3px rgba(0,0,0,.2), 0 4px 6px -4px rgba(0,0,0,.2);
  }}
  @media (min-width: 768px) {{ .topbar .links {{ display: flex; }} }}
  .topbar .links a {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 14px; color: var(--text); text-decoration: none;
  }}
  .topbar .links a:hover, .topbar .links a.active {{ color: #fff; }}
  .topbar .menu-btn {{
    display: block; background: none; border: 0; cursor: pointer;
    color: #e4e4e7; padding: 4px;
  }}
  .topbar .menu-btn:hover {{ color: #fff; }}
  @media (min-width: 768px) {{ .topbar .menu-btn {{ display: none; }} }}
  .mobile-menu {{
    position: fixed; inset: 0; z-index: 40; display: none;
    flex-direction: column; align-items: center; justify-content: center;
    gap: 36px; background: var(--bg);
  }}
  .mobile-menu.open {{ display: flex; }}
  .mobile-menu a {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 30px; color: var(--text); text-decoration: none;
  }}
  .mobile-menu a:hover, .mobile-menu a.active {{ color: #fff; }}
  a {{ color: var(--text); transition: color .15s ease; }}
  a:hover {{ color: #fff; }}

  h1 {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: clamp(34px, 6vw, 48px); font-weight: 700; color: var(--fg);
    margin: 10px 0 16px; cursor: default;
  }}

  /* section heading — `// label`, as on hackyguru.com */
  h2 {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 16px; font-weight: 600; color: var(--fg); margin-bottom: 16px;
  }}
  h2 .marker {{ margin-right: 8px; }}

  section {{ margin-top: 48px; }}

  .add {{
    border: 1px solid var(--border); border-radius: 12px;
    background: var(--card); padding: 18px 20px;
    transition: border-color .15s ease, background .15s ease;
  }}
  .add:hover {{ border-color: var(--border-hover); background: var(--card-hover); }}
  .add .getrow {{ display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }}
  .add .gettext {{ flex: 1; min-width: 260px; }}
  .add .getrow p {{ color: var(--dim); font-size: 13.5px; margin-top: 4px; max-width: 520px; }}
  .add .getrow .btn {{ margin-top: 14px; }}
  a.btn {{
    display: inline-block; background: var(--fg); color: #121212; border-radius: 8px;
    padding: 9px 18px; font-size: 13px; font-weight: 600; text-decoration: none;
    white-space: nowrap; transition: background .15s ease;
  }}
  a.btn:hover {{ background: #fff; color: #121212; }}
  /* Screenshot column: "Watch Tutorials" above the shot, with a blurred,
     slowly drifting copy of the shot glowing behind it. The glow is a
     decorative duplicate — the screenshot itself stays sharp and legible. */
  .add .shotwrap {{
    display: flex; flex-direction: column; align-items: center; gap: 12px;
    width: 320px; max-width: 100%; flex-shrink: 0; margin: 0;
  }}
  /* Glass button floating over the screenshot. backdrop-filter blurs the
     pixels behind it, so it reads as sitting on the image rather than
     punched into it; the shadow keeps it legible over light UI regions. */
  .add .watch {{
    position: absolute; z-index: 2; left: 50%; top: 50%;
    transform: translate(-50%, -50%);
    display: inline-flex; align-items: center; gap: 8px; white-space: nowrap;
    background: rgba(22,22,22,.42); color: #fff;
    border: 1px solid rgba(255,255,255,.38); border-radius: 999px;
    padding: 10px 20px; font-size: 13px; font-weight: 600;
    cursor: pointer; font-family: inherit;
    backdrop-filter: blur(9px) saturate(1.3);
    -webkit-backdrop-filter: blur(9px) saturate(1.3);
    box-shadow: 0 6px 22px rgba(0,0,0,.45);
    transition: background .15s ease, border-color .15s ease, transform .15s ease;
  }}
  .add .watch:hover {{
    background: rgba(38,38,38,.58); border-color: rgba(255,255,255,.7);
    transform: translate(-50%, -50%) scale(1.04);
  }}
  .add .watch:focus-visible {{ outline: 2px solid #fff; outline-offset: 3px; }}
  .add .watch-play {{
    width: 0; height: 0; border-style: solid; border-width: 5px 0 5px 8px;
    border-color: transparent transparent transparent #fff;
  }}
  .add .shotframe {{ position: relative; display: block; width: 100%; }}
  .add .shotglow {{
    position: absolute; inset: 6% 2%; z-index: 0; border-radius: 24px;
    background: url("basecamp.png") center/cover no-repeat;
    filter: blur(26px) saturate(1.6); opacity: .5; will-change: transform, opacity;
    animation: shotdrift 9s ease-in-out infinite alternate;
  }}
  @keyframes shotdrift {{
    from {{ transform: scale(1.01) translateY(0);    opacity: .38; }}
    to   {{ transform: scale(1.13) translateY(-6px); opacity: .72; }}
  }}
  .add .shot {{
    position: relative; z-index: 1; display: block; width: 100%; max-width: 100%;
    border: 1px solid var(--border); border-radius: 8px;
  }}

  /* Tutorial modal */
  .modal[hidden] {{ display: none; }}
  .modal {{ position: fixed; inset: 0; z-index: 80; display: flex; align-items: center; justify-content: center; padding: 20px; }}
  .modal-backdrop {{ position: absolute; inset: 0; background: rgba(0,0,0,.72); backdrop-filter: blur(6px); }}
  .modal {{ padding: 2vh 2vw; }}
  .modal-box {{
    position: relative; z-index: 1;
    width: min(1720px, 96vw); height: min(96vh, 1020px);
    display: flex; flex-direction: column; overflow: hidden;
    background: #161616; border: 1px solid var(--border-hover); border-radius: 14px;
    animation: modalin .18s ease-out;
  }}
  @keyframes modalin {{ from {{ opacity: 0; transform: translateY(8px) scale(.99); }} to {{ opacity: 1; transform: none; }} }}
  .modal-head {{
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 14px 16px; border-bottom: 1px solid var(--border);
  }}
  .modal-head h3 {{ font-size: 14px; font-weight: 600; }}
  .modal-head .sub {{ color: var(--dim); font-size: 12px; }}
  .modal-x {{
    background: transparent; color: var(--dim); border: 1px solid var(--border);
    border-radius: 8px; width: 30px; height: 30px; cursor: pointer; font-size: 13px;
    font-family: inherit; flex-shrink: 0; transition: color .15s ease, border-color .15s ease;
  }}
  .modal-x:hover {{ color: var(--fg); border-color: var(--border-hover); }}
  .modal-body {{ display: flex; min-height: 0; flex: 1; }}
  .tut-list {{
    width: 340px; flex-shrink: 0; overflow-y: auto; padding: 12px;
    border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 5px;
  }}
  .tut-item {{
    display: flex; flex-direction: column; gap: 3px; text-align: left; width: 100%;
    background: transparent; border: 1px solid transparent; border-radius: 9px;
    padding: 10px 12px; cursor: pointer; font-family: inherit; color: var(--dim);
    transition: background .15s ease, color .15s ease, border-color .15s ease;
  }}
  .tut-item:hover {{ background: var(--card-hover); color: var(--fg); }}
  .tut-item.current {{ background: var(--card-hover); border-color: var(--border-hover); color: var(--fg); }}
  .tut-n {{ font-size: 11px; letter-spacing: .04em; color: var(--dim); text-transform: uppercase; }}
  .tut-item.current .tut-n {{ color: var(--fg); }}
  .tut-t {{ font-size: 13px; line-height: 1.35; }}
  /* The player fills whatever space the (fixed-height) box leaves, instead of
     being capped by a 16:9 box — that's what makes the modal feel large.
     YouTube letterboxes inside it, which is invisible against the black. */
  .tut-player {{ flex: 1; min-width: 0; min-height: 0; background: #000; }}
  .tut-player iframe {{ width: 100%; height: 100%; border: 0; display: block; }}
  @media (max-width: 760px) {{
    .modal {{ padding: 0; }}
    .modal-box {{ width: 100vw; height: 100vh; border: 0; border-radius: 0; }}
    .modal-body {{ flex-direction: column; overflow-y: auto; }}
    .tut-list {{ width: 100%; border-right: 0; border-bottom: 1px solid var(--border); max-height: none; }}
    /* Stacked layout has no fixed height to fill, so restore the aspect box. */
    .tut-player {{ flex: none; }}
    .tut-player iframe {{ height: auto; aspect-ratio: 16 / 9; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .add .shotglow {{ animation: none; opacity: .45; }}
    .modal-box {{ animation: none; }}
    .add .watch:hover {{ transform: none; }}
  }}
  /* The section divider sits above the label, so the label reads as a
     heading for the URL row rather than floating between the two. */
  .add .urllabel {{
    margin-top: 20px; padding-top: 20px; border-top: 1px solid var(--border);
    color: var(--fg); font-size: 13.5px; font-weight: 600;
  }}
  .add .urlrow {{
    display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
    margin-top: 10px;
  }}
  .add code {{
    font-size: 13px; color: var(--fg); background: rgba(0,0,0,.35);
    border: 1px solid var(--border); border-radius: 8px; padding: 9px 12px;
    overflow-x: auto; flex: 1; min-width: 240px; white-space: nowrap;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }}
  .add button {{
    background: var(--fg); color: #121212; border: 0; border-radius: 8px;
    padding: 9px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
    font-family: inherit; transition: background .15s ease;
  }}
  .add button:hover {{ background: #fff; }}
  .add .hint {{ color: var(--dimmer); font-size: 12.5px; margin-top: 10px; }}

  .card {{
    border: 1px solid var(--border); border-radius: 12px; background: var(--card);
    padding: 20px; margin-bottom: 12px;
    transition: border-color .15s ease, background .15s ease;
  }}
  .card:hover {{ border-color: var(--border-hover); background: var(--card-hover); }}
  .card-head {{ display: flex; gap: 14px; align-items: center; margin-bottom: 12px; }}
  .icon {{
    width: 44px; height: 44px; border-radius: 10px; object-fit: contain;
    background: rgba(255,255,255,.05); border: 1px solid var(--border); flex-shrink: 0;
  }}
  .icon-fallback {{ display: flex; align-items: center; justify-content: center; color: var(--dim); font-size: 20px; }}
  h3 {{ font-size: 15px; font-weight: 600; color: #f3f4f6; transition: color .15s ease; }}
  .card:hover h3 {{ color: #fff; }}
  .ver {{ color: var(--dimmer); font-size: 12.5px; font-weight: 400; margin-left: 4px; }}
  .meta {{ color: var(--dimmer); font-size: 12px; }}
  .desc {{ color: var(--dim); font-size: 13.5px; line-height: 1.6; margin-bottom: 14px; }}
  .row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }}
  .label {{ color: var(--dimmer); font-size: 12px; min-width: 64px; }}
  .chip {{
    font-size: 12px; color: #f3f4f6; border: 1px solid var(--border);
    border-radius: 999px; padding: 2px 10px; background: rgba(255,255,255,.05);
  }}
  .chip-type {{ color: var(--dimmer); margin-left: 6px; }}
  .card-foot {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 14px; }}
  .card-foot .actions {{ display: flex; align-items: center; gap: 14px; }}
  a.btn-sm {{ padding: 6px 14px; font-size: 12px; }}
  .plats {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .plat {{
    font-size: 11.5px; color: var(--dimmer); border: 1px dashed var(--border);
    border-radius: 6px; padding: 1px 7px;
  }}
  a.src {{ color: var(--dim); font-size: 12.5px; text-decoration: none; }}
  a.src:hover {{ color: #fff; }}
  .empty {{ color: var(--dimmer); font-size: 14px; }}

  footer {{ margin-top: 72px; color: var(--dimmer); font-size: 12.5px; }}
  footer a {{ color: var(--dim); text-decoration: none; }}
  footer a:hover {{ color: #fff; }}
</style>
</head>
<body>
<div class="topbar" id="topbar">
  <nav>
    <a class="avatar" href="https://hackyguru.com" aria-label="Guru">
      <img src="head.gif" alt="Guru" width="40" height="40" decoding="async">
    </a>
    <div class="links">
      <a href="https://hackyguru.com/#about">about</a>
      <a href="https://hackyguru.com/articles">articles</a>
      <a href="https://hackyguru.com/talks">talks</a>
      <a href="https://hackyguru.com/awards">awards</a>
    </div>
    <button class="menu-btn" id="menu-btn" aria-label="Toggle menu">
      <svg id="icon-open" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" x2="20" y1="6" y2="6"/><line x1="4" x2="20" y1="12" y2="12"/><line x1="4" x2="20" y1="18" y2="18"/></svg>
      <svg id="icon-close" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:none"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
    </button>
  </nav>
</div>
<div class="mobile-menu" id="mobile-menu">
  <a href="https://hackyguru.com/#about">about</a>
  <a href="https://hackyguru.com/articles">articles</a>
  <a href="https://hackyguru.com/talks">talks</a>
  <a href="https://hackyguru.com/awards">awards</a>
</div>
<main>
  <header>
    <h1 id="title">guru's logos modules</h1>
  </header>

  <section>
    <h2><span class="marker">//</span>get basecamp</h2>
    <div class="add">
      <div class="getrow">
        <div class="gettext">
          <h3>New here? Download Logos Basecamp first</h3>
          <p>Basecamp is the desktop app these modules run in — node, wallet and
          package manager in a single install for macOS and Linux.</p>
          <a class="btn" href="https://logos.co/basecamp" target="_blank" rel="noopener">download ↗</a>
        </div>
        <figure class="shotwrap">
          <span class="shotframe">
            <span class="shotglow" aria-hidden="true"></span>
            <img class="shot" src="basecamp.png" alt="Logos Basecamp running the Persona module" loading="lazy">
            <button type="button" class="watch" id="watch-btn" aria-haspopup="dialog" aria-controls="tut-modal">
              <span class="watch-play" aria-hidden="true"></span>Watch Tutorials
            </button>
          </span>
        </figure>
      </div>
      <p class="urllabel">Add this catalog to your package manager</p>
      <div class="urlrow">
        <code id="repo-url">{html.escape(repo_url)}</code>
        <button onclick="navigator.clipboard.writeText(document.getElementById('repo-url').textContent).then(()=>{{this.textContent='copied';setTimeout(()=>this.textContent='copy',1500)}})">copy</button>
      </div>
      <p class="hint">Basecamp → Package Manager → Add repository. Versions and dependencies resolve automatically; packages are Ed25519-signed.</p>
    </div>
  </section>

  <section>
    <h2><span class="marker">//</span>modules</h2>
    {cards}
  </section>

  <footer>
    <span class="mono">index generated {generated}</span> ·
    <a href="{REPO_GH}">github</a> ·
    <a href="{repo['homepage']}/logos-repo.json">logos-repo.json</a>
  </footer>
</main>

<div class="modal" id="tut-modal" hidden>
  <div class="modal-backdrop" data-close></div>
  <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="tut-heading">
    <div class="modal-head">
      <div>
        <h3 id="tut-heading">Basecamp Tutorials</h3>
        <span class="sub mono">{len(TUTORIALS)} episodes · <a href="{PLAYLIST_URL}" target="_blank" rel="noopener">watch on youtube ↗</a></span>
      </div>
      <button class="modal-x" data-close aria-label="Close tutorials">✕</button>
    </div>
    <div class="modal-body">
      <nav class="tut-list" aria-label="Tutorial episodes">
{tut_items}
      </nav>
      <div class="tut-player">
        <iframe id="tut-frame" title="Basecamp tutorial video" allowfullscreen
                allow="accelerometer; encrypted-media; picture-in-picture; web-share"
                referrerpolicy="strict-origin-when-cross-origin"></iframe>
      </div>
    </div>
  </div>
</div>

<script>
  // Scramble-on-hover for the title, mirroring hackyguru.com's heading.
  (function () {{
    var el = document.getElementById("title");
    var text = el.textContent, timer = null;
    function scramble() {{
      var i = 0;
      clearInterval(timer);
      timer = setInterval(function () {{
        el.textContent = text
          .split("")
          .map(function (ch, j) {{
            if (ch === " " || j < i) return ch;
            return String.fromCharCode(65 + Math.floor(Math.random() * 60));
          }})
          .join("");
        i += 1;
        if (i > text.length) {{ clearInterval(timer); el.textContent = text; }}
      }}, 30);
    }}
    el.addEventListener("mouseover", scramble);
    scramble();
  }})();

  // Title bar: blur + border once scrolled, as on hackyguru.com.
  (function () {{
    var bar = document.getElementById("topbar");
    function onScroll() {{ bar.classList.toggle("scrolled", window.scrollY > 20); }}
    window.addEventListener("scroll", onScroll);
    onScroll();
  }})();

  // Mobile menu — full-screen overlay with scroll lock.
  (function () {{
    var btn = document.getElementById("menu-btn");
    var menu = document.getElementById("mobile-menu");
    var iconOpen = document.getElementById("icon-open");
    var iconClose = document.getElementById("icon-close");
    function setOpen(open) {{
      menu.classList.toggle("open", open);
      iconOpen.style.display = open ? "none" : "";
      iconClose.style.display = open ? "" : "none";
      document.body.style.overflow = open ? "hidden" : "";
    }}
    btn.addEventListener("click", function () {{ setOpen(!menu.classList.contains("open")); }});
    menu.addEventListener("click", function (e) {{ if (e.target.tagName === "A") setOpen(false); }});
  }})();

  // Tutorial modal. The iframe carries no src until the modal opens, so the
  // page makes no YouTube request unless the visitor asks for one — and the
  // src is cleared on close, which is what actually stops playback.
  (function () {{
    var modal = document.getElementById("tut-modal");
    var frame = document.getElementById("tut-frame");
    var open  = document.getElementById("watch-btn");
    var items = [].slice.call(modal.querySelectorAll(".tut-item"));
    if (!modal || !frame || !open || !items.length) return;

    function embed(id, autoplay) {{
      return "https://www.youtube-nocookie.com/embed/" + id +
             "?rel=0&modestbranding=1&playsinline=1" + (autoplay ? "&autoplay=1" : "");
    }}

    function select(item, autoplay) {{
      items.forEach(function (b) {{
        var on = b === item;
        b.classList.toggle("current", on);
        b.setAttribute("aria-current", on ? "true" : "false");
      }});
      frame.src = embed(item.dataset.id, autoplay);
      frame.title = "Part " + item.dataset.n + " — " + item.querySelector(".tut-t").textContent;
    }}

    function setOpen(isOpen) {{
      modal.hidden = !isOpen;
      document.body.style.overflow = isOpen ? "hidden" : "";
      if (isOpen) {{
        select(modal.querySelector(".tut-item.current") || items[0], false);
        modal.querySelector(".modal-x").focus();
      }} else {{
        frame.removeAttribute("src");   // stops playback
        open.focus();
      }}
    }}

    open.addEventListener("click", function () {{ setOpen(true); }});
    items.forEach(function (b) {{
      b.addEventListener("click", function () {{ select(b, true); }});
    }});
    modal.addEventListener("click", function (e) {{
      if (e.target.hasAttribute("data-close")) setOpen(false);
    }});
    document.addEventListener("keydown", function (e) {{
      if (e.key === "Escape" && !modal.hidden) setOpen(false);
    }});
  }})();
</script>
</body>
</html>"""

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.html"), "w") as f:
        f.write(page)
    # static assets served next to index.html (too big to inline)
    here = os.path.dirname(os.path.abspath(__file__))
    for asset in ("head.gif", "basecamp.png", "favicon.ico", "og.png"):
        src = os.path.join(here, asset)
        if os.path.exists(src):
            shutil.copy(src, args.out)
    # Same bytes as og.png, published under the hashed name the meta tags
    # point at — see the og_image comment above for why.
    shutil.copy(os.path.join(here, "og.png"), os.path.join(args.out, og_name))
    print(f"wrote {args.out}/index.html — {len(apps)} app(s), {len(index.get('packages', []))} package(s)")


if __name__ == "__main__":
    main()
