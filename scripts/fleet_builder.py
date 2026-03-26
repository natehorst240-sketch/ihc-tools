#!/usr/bin/env python3
"""
IHC Fleet Builder
=================
Interactive wizard that generates a lite fleet maintenance dashboard repo and
pushes it to a pre-created GitHub repository.

Lite dashboard includes 3 tabs:
  - Maintenance Due List
  - Flight Hours Tracking
  - Calendar

Usage:
    python scripts/fleet_builder.py

Requirements:
    pip install Pillow  (same as the dashboard itself)

The script creates a temporary directory, populates it with the dashboard
files configured to match your answers, then pushes to the target GitHub repo
using a Personal Access Token (PAT) with repo write scope.
"""

import sys
import os
import json
import shutil
import getpass
import tempfile
import subprocess
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Colour helpers for terminal output
# ---------------------------------------------------------------------------

def _c(code, text): return f"\033[{code}m{text}\033[0m"
def blue(t):   return _c("34", t)
def cyan(t):   return _c("36", t)
def green(t):  return _c("32", t)
def yellow(t): return _c("33", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)

# ---------------------------------------------------------------------------
# Pretty prompt helpers
# ---------------------------------------------------------------------------

def ask(prompt, default=None):
    """Prompt for a string value."""
    hint = f" [{dim(default)}]" if default else ""
    while True:
        raw = input(f"  {cyan('?')} {prompt}{hint}: ").strip()
        if raw:
            return raw
        if default is not None:
            return default
        print(f"  {yellow('!')} This field is required.")


def ask_optional(prompt, default=""):
    """Prompt for an optional string (Enter to skip)."""
    hint = f" [{dim(default)}]" if default else " [leave blank to skip]"
    raw = input(f"  {cyan('?')} {prompt}{hint}: ").strip()
    return raw or default


def ask_choice(prompt, choices, default=None):
    """Prompt for one of a fixed set of choices."""
    opts = "/".join(
        bold(c.upper()) if c == default else c for c in choices
    )
    while True:
        raw = input(f"  {cyan('?')} {prompt} ({opts}): ").strip().lower()
        if not raw and default:
            return default
        if raw in choices:
            return raw
        print(f"  {yellow('!')} Please enter one of: {', '.join(choices)}")


def ask_secret(prompt):
    """Prompt for a secret value (hidden input)."""
    while True:
        val = getpass.getpass(f"  {cyan('?')} {prompt}: ").strip()
        if val:
            return val
        print(f"  {yellow('!')} This field is required.")


def section(title):
    print()
    print(bold(f"── {title} {'─' * max(0, 52 - len(title))}"))


# ---------------------------------------------------------------------------
# Day-count parser  (accepts "30d", "6w", "3m", "1y", or plain integer)
# ---------------------------------------------------------------------------

def parse_days(raw):
    """
    Parse a human-friendly calendar period into an integer number of days.

    Accepted formats:
        30d  or  30   →  30 days
        6w           →  42 days
        3m           →  90 days
        1y           →  365 days
    """
    raw = raw.strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([dwmy]?)", raw)
    if not m:
        raise ValueError(f"Cannot parse '{raw}' as a day count.")
    n = float(m.group(1))
    unit = m.group(2) or "d"
    multiplier = {"d": 1, "w": 7, "m": 30, "y": 365}[unit]
    return int(round(n * multiplier))


# ---------------------------------------------------------------------------
# Colour palette for intervals
# ---------------------------------------------------------------------------

PALETTE = [
    "#00897b", "#1e88e5", "#8e24aa", "#e53935",
    "#fb8c00", "#43a047", "#6d4c41", "#d81b60",
    "#00acc1", "#5e35b1",
]

PALETTE_LABELS = [
    "teal", "blue", "purple", "red",
    "orange", "green", "brown", "pink",
    "cyan", "indigo",
]


def pick_color(idx):
    """Return a default colour for the nth interval."""
    return PALETTE[idx % len(PALETTE)]


# ---------------------------------------------------------------------------
# Wizard — collect answers
# ---------------------------------------------------------------------------

def run_wizard():
    print()
    print(bold("╔══════════════════════════════════════════════════════╗"))
    print(bold("║        IHC  FLEET  DASHBOARD  BUILDER  v1.0         ║"))
    print(bold("╚══════════════════════════════════════════════════════╝"))
    print(dim("  Generates a lite 3-tab dashboard and pushes it to a GitHub repo."))
    print(dim("  Tabs: Maintenance Due List · Flight Hours Tracking · Calendar"))

    # ── Organisation & Aircraft ──────────────────────────────────────────
    section("Organisation & Aircraft")

    org       = ask("Organisation name",    "Intermountain Health")
    ac_type   = ask("Aircraft type code (no spaces)", "AW109SP")
    ac_disp   = ask("Aircraft display name", f"AgustaWestland {ac_type}")
    tails_raw = ask("Tail numbers (comma-separated)", "N251HC, N261HC")
    tails     = [t.strip().upper() for t in tails_raw.split(",") if t.strip()]

    # ── Veryon CSV ───────────────────────────────────────────────────────
    section("Veryon Due-List CSV")
    print(dim("  This is the filename you will upload to the data/ folder."))
    csv_filename = ask("Veryon due-list CSV filename", f"Due-List_{ac_type}.csv")

    # ── Fleet Photo (optional) ───────────────────────────────────────────
    section("Fleet Photo  (optional)")
    print(dim("  If you have a fleet photo, place it in data/ with this filename."))
    photo_filename = ask_optional("Fleet photo filename", "fleet.jpeg")

    # ── Inspection Intervals ─────────────────────────────────────────────
    section("Inspection Intervals")
    print(dim("  Add each inspection interval. When finished, press Enter with no input."))
    print(dim("  track_by options:  h = hours only | d = days/calendar only | b = both"))
    print(dim("  Day counts:  30d = 30 days | 6w = 6 weeks | 3m = 3 months | 1y = 1 year"))
    print()

    intervals = []
    idx = 0

    while True:
        label = ask_optional(f"Interval #{idx+1} label (Enter to finish)", "")
        if not label:
            if not intervals:
                print(f"  {yellow('!')} Add at least one interval.")
                continue
            break

        track_by = ask_choice("  Tracked by", ["h", "d", "b"], default="h")

        hours = None
        days  = None

        if track_by in ("h", "b"):
            while True:
                raw_hrs = ask("  Interval size in hours", "100")
                try:
                    hours = int(float(raw_hrs))
                    break
                except ValueError:
                    print(f"  {yellow('!')} Enter a whole number (e.g. 100).")

        if track_by in ("d", "b"):
            while True:
                raw_days = ask("  Calendar period (e.g. 30d, 3m, 1y)", "30d")
                try:
                    days = parse_days(raw_days)
                    break
                except ValueError as e:
                    print(f"  {yellow('!')} {e}")

        track_by_str = {"h": "hours", "d": "days", "b": "both"}[track_by]

        # ATA pattern
        default_ata = f"05 {1000 + idx * 5:04d}"
        ata_raw = ask(f"  ATA regex pattern", default_ata)

        # Calendar duration
        while True:
            raw_dur = ask("  How many days does this inspection take?", "1")
            try:
                duration = max(1, int(float(raw_dur)))
                break
            except ValueError:
                print(f"  {yellow('!')} Enter a whole number.")

        # Colour
        default_color = pick_color(idx)
        color_idx_str = "/".join(f"{i+1}={PALETTE_LABELS[i]}" for i in range(len(PALETTE_LABELS)))
        print(f"  {dim('Colour palette:')} {color_idx_str}")
        color_raw = ask_optional("  Colour (hex like #00897b or palette number)", default_color)
        if color_raw.isdigit():
            n = int(color_raw)
            color = PALETTE[(n - 1) % len(PALETTE)]
        elif not color_raw.startswith("#"):
            color = default_color
        else:
            color = color_raw

        intervals.append({
            "label":                label,
            "hours":                hours,
            "days":                 days,
            "track_by":             track_by_str,
            "ata_patterns":         [ata_raw],
            "calendar_duration_days": duration,
            "color":                color,
        })
        idx += 1

    # ── Column Indices (Veryon CSV) ───────────────────────────────────────
    section("Veryon CSV Column Indices")
    print(dim("  The default indices match the standard Veryon due-list export."))
    print(dim("  Press Enter to accept defaults, or type a new 0-based column number."))

    defaults = {
        "reg":          0,
        "airframe_rpt": 2,
        "airframe_hrs": 3,
        "ata":          5,
        "equip_hrs":    7,
        "item_type":    11,
        "disposition":  13,
        "desc":         15,
        "interval_hrs": 30,
        "rem_days":     50,
        "rem_months":   52,
        "rem_hrs":      54,
        "status":       63,
    }
    col_indices = {}
    for name, default_val in defaults.items():
        raw = ask_optional(f"  Column '{name}'", str(default_val))
        try:
            col_indices[name] = int(raw)
        except ValueError:
            col_indices[name] = default_val

    # ── GitHub Target ────────────────────────────────────────────────────
    section("Target GitHub Repository")
    print(dim("  The repo must already exist (create it empty on GitHub first)."))
    print(dim("  PAT requires 'repo' (write) scope. Input is hidden."))

    target_repo = ask("Target repo (org/repo)", "myorg/fleet-dashboard")

    # Allow PAT to be pre-loaded via environment variable to avoid typing it
    _env_pat = os.environ.get("FLEET_BUILDER_PAT", "").strip()
    if _env_pat:
        print(f"  {green('✓')} PAT loaded from $FLEET_BUILDER_PAT environment variable.")
        pat = _env_pat
    else:
        pat = ask_secret("GitHub Personal Access Token (hidden)")

    return {
        "org":            org,
        "ac_type":        ac_type,
        "ac_disp":        ac_disp,
        "tails":          tails,
        "csv_filename":   csv_filename,
        "photo_filename": photo_filename,
        "intervals":      intervals,
        "col_indices":    col_indices,
        "target_repo":    target_repo,
        "pat":            pat,
    }


# ---------------------------------------------------------------------------
# Config JSON generator
# ---------------------------------------------------------------------------

def build_config(answers):
    ac_type   = answers["ac_type"]
    intervals = answers["intervals"]

    return {
        "_comment":      "Aircraft type configuration for the IHC Fleet Dashboard generator.",
        "aircraft_type": ac_type,
        "display_name":  answers["ac_disp"],
        "organization":  answers["org"],

        "due_list_filename":       answers["csv_filename"],
        "due_list_fallbacks":      [],
        "photo_filename":          answers["photo_filename"] or "",
        "output_filename":         "index.html",

        "component_window_hrs":  200,
        "component_window_days": 60,

        "retirement_keywords": [
            "RETIRE", "OVERHAUL", "DISCARD", "LIFE LIMIT", "TBO",
            "REPLACEMENT", "REPLACE", "CHANGE OIL", "NOZZLE"
        ],

        "_col_comment": "0-based column indices in the due-list CSV export from Veryon.",
        "col_indices":  answers["col_indices"],

        "_interval_comment": (
            "Inspection intervals. 'hours': interval in flight hours (null = date-only). "
            "'days': calendar interval in days (null = hours-only). "
            "'track_by': 'hours', 'days', or 'both'."
        ),
        "inspection_intervals": intervals,
    }


# ---------------------------------------------------------------------------
# GitHub Actions workflow templates
# ---------------------------------------------------------------------------

def build_workflow(answers):
    csv_filename = answers["csv_filename"]
    ac_type      = answers["ac_type"]

    return f"""\
name: Build Dashboard
on:
  schedule:
    - cron: "0 */6 * * *"
  push:
    branches: ["main"]
    paths:
      - "data/{csv_filename}"
  workflow_dispatch:
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: pip install Pillow
      - name: Build dashboard
        run: python scripts/fleet_dashboard_generator.py --config configs/{ac_type.lower()}.json
      - name: Commit output
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add data/index.html data/flight_hours_history.json data/dashboard_version.json
          git diff --staged --quiet || git commit -m "ci: rebuild dashboard"
          git push
"""


def build_deploy_workflow():
    return """\
name: Deploy Dashboard to GitHub Pages

on:
  push:
    branches: ["main"]
    paths:
      - "data/**"
      - ".github/workflows/deploy-pages.yml"
      - "scripts/fleet_dashboard_generator.py"
  workflow_run:
    workflows: ["Build Dashboard"]
    types: [completed]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  deploy:
    if: ${{ github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success' }}
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.x"

      - name: Install dependencies
        run: pip install Pillow

      - name: Regenerate dashboard
        run: python scripts/fleet_dashboard_generator.py

      - name: Setup Pages
        uses: actions/configure-pages@v5

      - name: Upload artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: "data"

      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
"""


def build_readme(answers):
    org          = answers["org"]
    ac_type      = answers["ac_type"]
    ac_disp      = answers["ac_disp"]
    csv_filename = answers["csv_filename"]
    tails        = answers["tails"]

    tails_list = "\n".join(f"- {t}" for t in tails)

    return f"""\
# {org} Fleet Maintenance Dashboard

Automated maintenance tracking dashboard for the **{ac_disp}** fleet.
Generated by the [IHC Fleet Builder](https://github.com/natehorst240-sketch/ihc-tools).

## Tabs

| Tab | Description |
|-----|-------------|
| Maintenance Due List | Phase inspection hours remaining, colour-coded urgency |
| Flight Hours Tracking | Utilisation rates, daily/weekly/monthly averages |
| Calendar | Projected inspection dates with drag-and-drop notes |

## Aircraft Tracked

{tails_list}

## Setup

### 1. Enable GitHub Pages

Go to **Settings → Pages** and set:
- Source: **GitHub Actions**

### 2. Upload your Veryon due-list CSV

Export from Veryon and upload to `data/{csv_filename}`.

Pushing this file to `main` automatically triggers a rebuild.

### 3. (Optional) Add a fleet photo

Place a JPEG in `data/` named as configured in `configs/{ac_type.lower()}.json`
(`photo_filename` key) and commit it.

## Rebuilding Manually

Go to **Actions → Build Dashboard → Run workflow**.

## Configuration

Edit `configs/{ac_type.lower()}.json` to adjust inspection intervals, ATA patterns,
or Veryon column indices without touching any Python code.
"""


# ---------------------------------------------------------------------------
# Repo assembly
# ---------------------------------------------------------------------------

def find_lite_generator():
    """Locate fleet_dashboard_generator_lite.py relative to this script."""
    script_dir = Path(__file__).parent.resolve()
    candidates = [
        script_dir / "fleet_dashboard_generator_lite.py",
        script_dir / "templates" / "fleet_dashboard_generator_lite.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def assemble_repo(answers, tmp_dir):
    """
    Populate tmp_dir with a complete repo ready to push.
    Returns True on success, False if the lite generator cannot be found.
    """
    tmp = Path(tmp_dir)
    ac_type = answers["ac_type"].lower()

    # Locate the lite generator
    gen_src = find_lite_generator()
    if gen_src is None:
        print(
            f"\n  {yellow('!')} Could not find fleet_dashboard_generator_lite.py.\n"
            f"  Make sure it lives next to this script or in a templates/ subfolder."
        )
        return False

    # ── Directory structure ────────────────────────────────────────────
    (tmp / ".github" / "workflows").mkdir(parents=True)
    (tmp / "configs").mkdir()
    (tmp / "data").mkdir()
    (tmp / "scripts").mkdir()

    # ── Generator ─────────────────────────────────────────────────────
    shutil.copy(gen_src, tmp / "scripts" / "fleet_dashboard_generator.py")

    # ── Config JSON ───────────────────────────────────────────────────
    config = build_config(answers)
    cfg_path = tmp / "configs" / f"{ac_type}.json"
    cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    # ── GitHub Actions workflows ───────────────────────────────────────
    (tmp / ".github" / "workflows" / "build_dashboard.yml").write_text(
        build_workflow(answers), encoding="utf-8"
    )
    (tmp / ".github" / "workflows" / "deploy-pages.yml").write_text(
        build_deploy_workflow(), encoding="utf-8"
    )

    # ── data/ placeholder ─────────────────────────────────────────────
    (tmp / "data" / ".gitkeep").write_text(
        f"# Upload your Veryon due-list CSV ({answers['csv_filename']}) here.\n",
        encoding="utf-8",
    )

    # ── requirements.txt ─────────────────────────────────────────────
    (tmp / "requirements.txt").write_text("Pillow>=10.0.0\n", encoding="utf-8")

    # ── README ────────────────────────────────────────────────────────
    (tmp / "README.md").write_text(build_readme(answers), encoding="utf-8")

    return True


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def run_git(args, cwd, env=None, check=True):
    """Run a git command, printing it and returning CompletedProcess."""
    cmd = ["git"] + args
    print(f"  {dim('$')} {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)
    if result.stdout.strip():
        print(f"    {dim(result.stdout.strip())}")
    if result.stderr.strip():
        print(f"    {dim(result.stderr.strip())}")
    if check and result.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed (exit {result.returncode})")
    return result


def push_repo(answers, tmp_dir):
    """Init git, commit all files, and push to the target repo using the PAT."""
    tmp         = Path(tmp_dir)
    target_repo = answers["target_repo"]
    pat         = answers["pat"]
    remote_url  = f"https://{pat}@github.com/{target_repo}.git"

    print()
    print(bold("── Pushing to GitHub ───────────────────────────────────────"))

    # Configure git identity for this repo only
    run_git(["init", "-b", "main"], cwd=tmp)
    run_git(["config", "user.name",  "IHC Fleet Builder"], cwd=tmp)
    run_git(["config", "user.email", "fleet-builder@noreply"], cwd=tmp)

    run_git(["add", "-A"], cwd=tmp)
    run_git(["commit", "-m", "feat: initial fleet dashboard (generated by IHC Fleet Builder)"], cwd=tmp)

    # Use credential helper env to avoid leaking PAT into process list
    env = {**os.environ, "GIT_ASKPASS": "echo", "GIT_TERMINAL_PROMPT": "0"}
    run_git(["remote", "add", "origin", remote_url], cwd=tmp)

    # Retry with exponential backoff on network errors
    import time
    for attempt, wait in enumerate([0, 2, 4, 8, 16]):
        if wait:
            print(f"  {yellow('!')} Push failed. Retrying in {wait}s...")
            time.sleep(wait)
        result = run_git(
            ["push", "-u", "origin", "main"],
            cwd=tmp, env=env, check=False
        )
        if result.returncode == 0:
            return True
        # 403 = auth failure — no point retrying
        if "403" in (result.stderr or "") or "authentication" in (result.stderr or "").lower():
            print(f"\n  {yellow('ERROR:')} Push rejected (403 / auth failure).")
            print("  Check that the PAT has 'repo' write scope and the repo exists.")
            return False
    print(f"\n  {yellow('ERROR:')} Push failed after 5 attempts (network error).")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    answers = run_wizard()

    print()
    print(bold("── Summary ─────────────────────────────────────────────────"))
    print(f"  Organisation : {answers['org']}")
    print(f"  Aircraft     : {answers['ac_disp']}  ({answers['ac_type']})")
    print(f"  Tails        : {', '.join(answers['tails'])}")
    print(f"  Intervals    : {', '.join(iv['label'] for iv in answers['intervals'])}")
    print(f"  Veryon CSV   : {answers['csv_filename']}")
    print(f"  Target repo  : {answers['target_repo']}")
    print()

    confirm = ask_choice("Build and push this dashboard?", ["y", "n"], default="y")
    if confirm != "y":
        print("  Aborted.")
        sys.exit(0)

    with tempfile.TemporaryDirectory(prefix="ihc-fleet-build-") as tmp_dir:
        print()
        print(bold("── Assembling repo ─────────────────────────────────────────"))
        ok = assemble_repo(answers, tmp_dir)
        if not ok:
            sys.exit(1)
        print(f"  {green('✓')} Repo assembled in {tmp_dir}")

        success = push_repo(answers, tmp_dir)

    if success:
        print()
        print(green(bold("  ✓ Done!")))
        print(f"  Repo: https://github.com/{answers['target_repo']}")
        print()
        print("  Next steps:")
        print("    1. Enable GitHub Pages: Settings → Pages → Source: GitHub Actions")
        print(f"    2. Upload your Veryon CSV to data/{answers['csv_filename']}")
        print("    3. The dashboard builds automatically on each push.")
        print()
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
