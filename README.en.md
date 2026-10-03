# mcmigrator

[中文](README.zh-CN.md) | [🏠 Landing](README.md)

> ℹ️ Community translation. The [Chinese version](README.zh-CN.md) is the authoritative source and may be ahead of this translation.
> Last synced: v1.0.0 / 2026-10-04

> A Minecraft modpack version-migration tool — scan, diff, plan, migrate, modpack swap, plus a local web wizard.

When your modpack moves from one NeoForge version folder to another, you want to know: **which files does the player need to keep or update in the new version?** `mcmigrator` scans version folders with `scan`, compares snapshots with `diff` (migration-oriented 6-bucket report), migrates player state end-to-end with `plan`/`migrate`, and swaps whole modpacks with `swap`; every write is backed up first and can be previewed with `--dry-run`, and tool-owned data lives in `.mcmig/` (layout detailed in *Data & Uninstall*).

## Which Package to Choose

| Artifact | Form | Recommendation |
|---|---|---|
| `mcmig-<version>-win-x64.zip` | **onedir portable folder** | **Recommended**: unzip and run; contains `mcmig.exe` (CLI) and `mcmig-gui.exe` (window); fast start, smaller footprint |
| `mcmig-gui-<version>-win-x64.exe` | onefile single exe | For a quick try: double-click window build; unpacks to a temp dir on every start (slower) and has a higher antivirus false-positive rate |

## Download

Grab either artifact from the [Releases](https://github.com/Nothing-ray/mcmigrator/releases) page; each release ships a `SHA256SUMS.txt` (SHA256 list for both artifacts). Verify after downloading (built-in Windows command):

```bat
certutil -hashfile mcmig-<version>-win-x64.zip SHA256
```

A match with the entry in `SHA256SUMS.txt` means the download is intact; otherwise re-download.

## Updating (Basic Tier)

- **CLI**: `mcmig update [--check]` — check for a newer release (`--check` only checks); if found, download → enforce SHA256 → stage, then print the **three replacement steps** for your install form:
  - **onedir portable folder**: (1) quit every mcmig process (window and console); (2) unzip the downloaded zip and copy the extracted `mcmig` folder contents over your existing `mcmig` program folder (overwrite same-named files; your own `data/config.toml` is kept); (3) restart. The staging folder can be deleted afterwards.
  - **onefile single exe**: (1) quit the running mcmig; (2) replace the `mcmig-gui*.exe` you currently use with the downloaded exe (overwrite or rename); (3) restart. The staging folder can be deleted afterwards.
- **GUI**: step ① "About / Update" panel — check / download & verify (cancellable; progress survives a page refresh); the done state shows "**Downloaded, not yet applied**" with an *open staging location* button and the same form-specific replacement instructions.
- **Behavior contract**: the tool **never runs downloaded files** and offers no "run now" button; source checkouts don't offer downloads (use `git pull`). A failed checksum or malformed manifest rejects the download and cleans up.
- Automatic (in-place) updates arrive in a later release; today's flow is **download – verify – replace manually**.

## Features

- **Tiered hashing**: full MD5 for text, filename-set for mods, size proxy for bulk (`.sqlite`/`.zip`/`.mca`) — fast and precise (byte-level for text the player edits; size proxy for binaries they don't).
- **Data-driven classification**: rule engine (`pathspec`, gitignore semantics), layered first-match-wins (CLI override > user rules > built-in default > unknown); changing rules doesn't require rescanning.
- **Migration-oriented 6-bucket diff**: `to_migrate` / `candidate` / `mods` (by filename set) / `only_in_dst` / `identical` / `never`.
- **Game-content zero writes**: only writes its own `.mcmig/` (snapshots/plans/job journals); never touches mods/config/saves.

## Installation

Requires Python 3.11+.

```bash
git clone https://github.com/Nothing-ray/mcmigrator.git
cd mcmigrator
python -m venv .venv
.venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -e .
```

## Configuring the Game Root

`mcmig` needs to know your game root (the directory containing `versions/`). Three options, highest priority first:

1. **Command flag**: `mcmig scan <ver> --game-root <absolute path>`
2. **Environment variable**: set `MCMIG_GAME_ROOT`
3. **Config file**: `cp config.example.yaml .mcmig/config.yaml`, edit `game_root` in it (source runs; with the portable exe, save it from the wizard's step-① "game root" input box, which persists to `data/config.toml`)

If none is provided, the tool errors out with the above guidance.

## Quick Start

```bash
mcmig scan 1.21.1-NeoForge_21.1.227                              # scan → snapshot + classification summary
mcmig scan 1.21.1-NeoForge_21.1.229
mcmig diff 1.21.1-NeoForge_21.1.227 1.21.1-NeoForge_21.1.229     # 6-bucket report (rich)
mcmig diff <src> <dst> --json                                     # JSON output
mcmig diff <src> <dst> --exclude "logs/**"                        # ad-hoc treat as never
mcmig diff <src> <dst> --show-identical --show-never              # show hidden buckets
```

### Command Overview

| Command | Purpose |
|---------|---------|
| `mcmig scan <ver>` | Scan a version folder, producing a snapshot + classification summary |
| `mcmig diff <src> <dst>` | Compare two snapshots into a 6-bucket report |
| `mcmig diff <src> <dst> --modpack-swap` | Swap-acceptance view: source-only mods land in "swapped out" instead of to_add; pairing marks are kept (a new mod marked `⇄upgrade` is an upgrade, not a brand-new addition) |
| `mcmig plan <src> <dst>` | Generate a migration plan (read-only, produces an action list) |
| `mcmig migrate <src> <dst>` | Execute the saved migration plan (plan first, then migrate; overwrites are auto-backed up to `_conflict_backup/`) |
| `mcmig swap <src> <dst> <new-pack-dir>` | Modpack swap: compatibility precheck → install pack → generate swap migration plan |
| `mcmig doctor` | Environment health check: data integrity / game root config / permissions / disk space |
| `mcmig-gui` | Launch the migration wizard as a standalone window (WebView2 renderer; falls back to browser mode automatically when unavailable) |
| `mcmig gui [--port N] [--no-browser] [--window]` | Launch the local web migration wizard (auto-opens the browser; random free port by default; `--window` = the standalone window) |

> `<version>` = `versions/` subfolder name (MC + loader, e.g. `1.21.1-NeoForge_21.1.227` = Minecraft 1.21.1 + NeoForge 21.1.227).

## Graphical Interface

A three-step migration wizard: ① pick versions → ② review the plan → ③ execute. Two ways to launch:

- **Standalone window**: `mcmig-gui` (or `mcmig gui --window`) — pywebview + WebView2 rendering. Before starting it runs a data-manifest self-check and a renderer precheck; if pywebview is missing, the WebView2 runtime is absent, or the window fails to start, it **falls back to browser mode automatically** (with a printed hint; the precheck happens before the window opens, so no obsolete MSHTML window ever flashes). Closing the window while a job is running is blocked with a notice (wait for completion or cancel); closing while idle exits directly.
- **Browser mode**: `mcmig gui` (random free port by default, auto-opens the browser; `--no-browser` skips opening).

Wizard capabilities as of v0.12:

- **Review-page summary**: the plan page header shows five key figures — N items to migrate / approx. size / M to confirm / O to be overwritten (backed up) / K compat warnings — plus a "detection scope" note stating exactly what the summary does and does not promise; mod-pairing terms are localized (upgrade / renamed / rebuilt), and the diff summary and warnings now arrive with the plan instead of living only in server logs.
- **Two-stage swap**: the swap panel first issues a **read-only preflight** (`/api/swap/preflight`), presenting three decision lists (mods in the new pack incompatible with the target NeoForge / leftover jars in the target's mods/ that the new pack lacks / same-name jars with different content). After confirmation it **applies the install** (`/api/swap/apply`; the server re-verifies under the instance lock: input fingerprint recompute + version-pair identity + compat rerun), then chain-rescans and generates the swap migration plan within the same job, landing directly on the review page (old-pack-only jars are not migrated back). Overwritten jars are backed up first to `<game-root>/.mcmig/backups/swap/<UTC timestamp>/`; once the install completes and the replanning phase begins, the cancel entry is hidden (that phase is not cancellable — a cancel request gets 409).
- **Refresh recovery & reconnect**: refresh the page while a job is running and progress resumes from the event cursor (no rescan, no replay); on a network blip the page shows "connection lost, reconnecting…", and after recovery events are deduplicated by sequence number — the browser reconnects with the `Last-Event-ID` request header (which takes priority over a stale cursor parameter in the subscription URL).
- **Interruption recovery**: write-ahead journals for migration and swap-install land in `<game-root>/.mcmig/jobs/`; after the server exits abnormally and restarts, a banner at the top of the page lists the interrupted job's pending-review items — confirm them against the guidance, then dismiss entries one by one (dismissal is rejected with 409 while that job is still alive); journals of jobs that finished normally are swept automatically the next time the interrupted list is read, so nothing accumulates.

## How It Works

1. `scan` traverses the version folder, hashes by the tiered strategy, and produces a **raw manifest snapshot** (`<game_root>/.mcmig/snapshots/<ver>.snapshot.json`, **no classification**). The snapshot carries an optional identity field `resolved_root` (the version directory `resolve()`d at scan time — the real path behind any NTFS junction); older snapshots default it to `None`, fully compatible. As of v2, the snapshot also embeds a list of your mods — `scan` reads mod info out of the jars in `mods/` along the way (adds roughly 1–3 seconds); old v1 snapshots remain fully compatible, just rescan once to upgrade.
2. `diff` reads two snapshots, **classifies by current rules on the fly**, and assigns each file to one of 6 buckets.
3. After changing rules (user `.mcmig/rules.yaml` or CLI `--exclude`/`--include`), **re-run `diff` without rescanning** — classification is computed at snapshot-read time.

### Tiered Hashing

| File type | Basis | Reason |
|---|---|---|
| Text (`config/`, `options.txt`, `*.dat`, scripts) | Full MD5 | Players edit these; need byte-level precision |
| `mods/**/*.jar` | Filename set | Players don't edit jar internals; version change = filename change |
| `*.sqlite` / `*.zip` / `*.mca` | size | Bulk-replace type; size is a good proxy |

`--strict` forces full hashing as an escape hatch.

## Classification System

`mcmig plan` assigns each file an **origin** (semantic source) that determines the migration behavior:

| Origin | Behavior | Meaning | Example |
|--------|----------|---------|---------|
| ✅ Must-migrate | Copy | Core player data, irreversible if lost | `options.txt`, `saves/`, `local/ftbchunks/` |
| ✏️ Modified config | Copy | Has `.bak` with different content = player edited in-game | `config/create-client.toml` |
| 📋 Backup file | Copy | `.bak` file (never by default — the backup itself is not migrated; follows its parent config only when a user rule promotes it) | `config/create-1.toml.bak` |
| 📦 Add mod | Copy | Source-only mod (player-added) | `mods/extra.jar` |
| 📦 Swapped out | Skip | With `--modpack-swap`, source-only mods are treated as old-pack built-ins, not migrated back; explicit `must_migrate` in rules.yaml still passes; **pairing marks kept** (a new mod marked `⇄upgrade` = an upgrade, not a brand-new addition) | Old modpack's `mods/old-pack.jar` |
| ❓ Needs review | Ask | No reliable auto-detection, needs manual confirmation | `kubejs/**`, `resourcepacks/*.zip` |
| 👻 Orphan data | Skip | Corresponding mod not installed in target; migrating is pointless | `config/jade/**` (Jade removed) |
| 🔒 Version-sensitive | Skip | Version/hardware-derived, high-risk across versions, let target rebuild | `config/fml.toml` |
| ⚙️ Default config | Skip | No `.bak` (mod default), or `.bak` content identical to config (auto-generated) | `config/patchouli-client.toml` |
| ⛔ Never | Skip | Temp artifacts / version binaries / caches | `logs/`, `<ver>.jar` |
| ⏭ Identical | Skip | Both sides have identical content | Files with matching MD5 |
| 📦 Shared mod | Skip | Mod present in both source and target | — |
| 📦 Target-only mod | Skip | Mod present in target but not source | — |

### Priority

Rules match first-match-wins, from highest to lowest priority:

```
CLI (--include/--exclude) > Extra rule files > User rules.yaml > Orphan detection > Version-sensitive > Whitelist > Built-in default
> World dirs (dynamic detection)
```

- **User explicit rules > Orphan detection**: Writing `config/jade/** → must_migrate` in `.mcmig/rules.yaml` forces migration of orphan configs.
- **Orphan detection > Whitelist**: Whitelist entries whose corresponding mod has been removed are automatically voided.
- **Orphan detection = factual judgment**: Mod physically absent from target's `mods/` directory → config has no owner → migrating is pointless.
- **The world-dir layer sits at the bottom**: world directories detected dynamically on the server side are injected as must-migrate after every other layer — built-in default never rules (e.g. a `.bak` inside the world) still override it, so nothing slips in by mistake.

### Modpack Swap

When replacing the entire modpack (not upgrading within the same pack), add `--modpack-swap`: source-only mods are no longer migrated back (old-pack built-ins, not player extras), though jars explicitly marked `must_migrate` in user `rules.yaml` still pass. Without the flag, the tool prints a hint to stderr when it detects ≥ 20 source-only mods.

Full swap workflow:

```bash
mcmig swap <old-version> <new-version> <new-pack-dir>   # precheck + install + plan (aborts if NeoForge requirement unmet; follow the hint; dry-run does not generate a migration plan)
mcmig migrate <old-version> <new-version>               # review the plan, then execute the copy
```

Create the new version folder and install its NeoForge via PCL2 first. `migrate` is resumable (re-run after an interruption continues where it left off). In the GUI, swap is a two-stage interaction (read-only preflight → confirm install + chain-generated swap plan); see *Graphical Interface*.

### .bak Heuristic

NeoForge auto-generates `.bak` backups when a player edits a config in-game. The tool compares the MD5 of the config and its `.bak`:

- **MD5 differs** → `.bak` stores the pre-edit version → player did modify → migrate
- **MD5 identical** → `.bak` backup matches current → mod auto-generated (not player-edited) → skip

### diff mods-bucket semantics & pairing

diff reports from the **migration-source frame**: src = source (old instance), dst = target (new instance).
mods-bucket notes: `shared` = same-named jar on both sides; `to_add` = **src-only** (migrated over);
`target_only` = shipped by target. Upgrades/renames are paired by modid (rich-table `⇄upgrade`/`⇄renamed`
markers + a pairing footnote; top-level `mod_pairs` array in `--json` output) instead of unrelated
remove+add pairs. Pair kinds: upgrade `⇄upgrade` / rename `⇄renamed` unchanged; `⇄rebuilt` = a
same-version-number repack whose filename carries a -Patch/-feature-style suffix (warning prefix ⚠,
same semantics as the same-name rebuilt bucket).
Filename pairing falls back through **five lattice levels** (table-driven: each level = pairing key + precondition + kind judgment; a generic loop consumes each level's leftovers from the one above, so adding a new pairing shape is one lattice entry): ① an identical full family key; ② the variant
suffix stripped (same version → rebuilt); ③ suffix stripped, version upgraded (e.g.
`1.1.8-feature` → `1.1.9-fix`); ④ platform decoration words (`neoforge`/`forge`/`fabric`/`mc`…)
stripped as well (the author changed naming style); ⑤ finally a **closed set of decoration words** stripped (`all`/`patch`/`fix`/`feature`/`release`/`up`/`port`/`api`/`lib`/`compat`, removed wherever they appear in the family key; words outside the closed set are never stripped, to prevent false pairs). A candidate only reaches the next level when
the previous one failed to pair it, and registry (modid) pairing always takes priority.

- `rebuilt`: same name and version on both sides but different content (upstream repack) — flagged in diff; plan keeps the target side by default with a warning, never auto-overwrites
- `⇄renamed(rebuilt)` ⚠: a rename pair with the **same version number but differing size on the two sides** (upstream-repack evidence) — the kind stays `renamed`, with a `content_differs` annotation added (`--json` `mod_pairs` entries emit that key only when true, zero noise for consumers; plan COPY rows mirror `⇄renamed(rebuilt)`), so a renamed pair is never mistaken for "byte-identical, safe to skip deploying"
- `mod_pairs` entries carry a `source` field: `registry` (reads mods.toml inside the jar; requires the two version dirs to be truly independent) or `filename` (snapshot filename-family normalization; works for replay/junction setups)
- The `plan` report likewise decorates paired jars' COPY-row paths with `⇄<kind>` (a `rebuilt` pair gets the `⚠` prefix, mirroring diff semantics); the decoration is render-level only — the persisted `plan.json` carries no pairing (schema unchanged)
- When both version dirs resolve to the same path (NTFS junction), registry pairing is automatically voided with a hint and filename pairing takes over (two snapshots of the same directory taken at different times — the standard shadow-root usage — are no longer hinted, logged at debug level only; same-timestamp self-comparison still warns). Self-comparison detection is now single-point: the same snapshot file is the primary verdict; junction setups fall back to "same dir + same timestamp"; replays (no live directory) emit a "suspected self-comparison" corroboration hint when the snapshots' `resolved_root` values are equal and timestamps match
- mtime evolution channel: un-hashed files (bulk/mods entries, md5=null) record `mtime` in the snapshot; the channel activates only when both snapshots' `resolved_root` is the **same physical root** (segmented snapshots of one instance / junction setups) — a same-size rewrite with a different mtime is then reported as modified (note=`mtime`, displayed in the terminal as `mtime(同尺寸重写)`, "same-size rewrite"); cross-instance migration (copying always changes mtime) and older snapshots (no such field) keep it off
- When a mod was removed on the target side, its config is flagged as orphan (`never/orphan`) — standalone `diff` now matches `plan`
- `*.properties` (e.g. server `server.properties`; vanilla rewrite noise — escaping/timestamp/encoding) and `*.json`/`*.toml` (key/table-order noise from mod startup rewrites) that differ in bytes but not in key/value semantics are reported as `identical/semantics` instead of modified
- JVM crash remnants (`hs_err_pid*.log` / `replay_pid*.log`) go to the never bucket, never migrated
- `*.bak` backup files (in **any** directory, not just config; generation-accumulated markers) also go to
  the never bucket — the backup itself is not migrated, the `.bak` heuristic is unaffected, and a user rule
  can promote it back
- Orphan flagging and semantic re-checks require the snapshot's `game_root` to be reachable; otherwise diff degrades to pure byte comparison with a one-line stderr hint

### Known client-only mod list

`migration/data/client_mods.yaml` keeps a list of known client-only mods (constructor-crash risks when deploying to a dedicated server; the first entry, `glacier_dragon`/`frost-dragon`, comes from a real r11 dedicated-server crash). Each entry may provide either or both matching keys — `modid` (live registry channel) and `family` (filename-family key, works in replay mode) — plus a `reason` documenting the evidence. During `diff`, mods-bucket rows that hit the list get a `client_only` annotation plus a one-line stderr warning. **Annotation only, never a block** — the tool is a differ, not a deployer; whether to exclude is your call. Embedded (JarInJar) client-only components are visible too: when a modid hit is an embedded module (e.g. the anima renderer embedded inside damage-engine), the annotation lands on the **host jar's path** — the physical artifact a dedicated server actually needs to isolate; the list has taken in `damageengine`/`anima` (evidence: a real r12 dedicated-server constructor crash). To extend the list: add an entry to that yaml, then re-run `tools/gen_manifest.py` to refresh the data-integrity manifest.

## Data & Uninstall

### Where the Tool Keeps Its Data (path contract v3)

Tool state splits into two layers: the **global layer** (travels with the tool) and the **instance layer** (travels with the game instance; the CLI and GUI read/write the same location):

```
mcmig/ (exe folder)                  <game-root>/.mcmig/ (instance layer, CLI/GUI shared)
├── mcmig-gui.exe / mcmig.exe        ├── snapshots/ plans/ rules.yaml
└── data/                            ├── jobs/ (job journal; interrupted = pending review)
    └── config.toml (global only)    ├── locks/ (reserved) / backups/swap/ (swap overwrite backups)
```

- **Global layer (software side)**: with the portable exe it is `data/config.toml`, holding **only global config** (the game-root pointer) — nothing is ever written to AppData or user directories, and copying the whole client folder carries the config along. For source runs it is the working directory's `.mcmig/config.yaml` (unchanged). A first run with nothing configured is a welcome state (no error); the wizard's step-① input box guides you to set and persist it.
- **Instance layer (`<game-root>/.mcmig/`)**: snapshots (`snapshots/`), migration plans (`plans/`), user rules (`rules.yaml`), and job journals (`jobs/` — the write-ahead journal for migration and swap-install; after an abnormal exit it feeds the page's "pending review" banner, and finished archives are swept automatically) are all anchored at the game root, **same location** for both portable and source modes, so multiple modpack roots never mix. `locks/` is the cross-process instance-lock registry (reserved); `backups/swap/<UTC timestamp>/` holds swap-install overwrite backups (from Batch I W3).
- **Migrating from the old layout**: the old portable layout `exe/data/<game-name>/snapshots|plans|rules.yaml` and the old source-mode instance state under `cwd/.mcmig/` are **read-only fallbacks** — the tool never writes to the old locations; legacy snapshots are still read, with a hint recommending a bulk move; when `rules.yaml` exists in both places, **the new location wins** (the old file is ignored with a notice). The "using legacy-layout plan" hint is a **CLI-side** (`mcmig migrate`) behavior — the GUI always rescans both sides when generating a plan, so snapshots and plan files always land at the anchored location (legacy snapshots only produce a read-only notice and never participate in locating). Recommended: move old `snapshots/` and `rules.yaml` into `<game-root>/.mcmig/` as a whole, then delete the old copies.

### What Gets Written on the Game Side

The only directory the tool ever creates inside the game root is `.mcmig/` (snapshots, migration plans, user rules, and job journals — pure tool artifacts; snapshots rebuild on re-scan; job journals keep only unfinished archives: finished ones are swept automatically the next time the interrupted list is read, and deleting an unfinished journal merely clears the "pending review" banner); no other tool directory is created. The only thing written to game **content** during migration is the **conflict backup**: a file with the same name but different content is backed up to `<target-version>/_conflict_backup/` before being overwritten (mirroring the relative path; the first backup is the pre-overwrite original, and re-runs never overwrite it). A swap, by contrast, installs the new pack's `mods/` into the target version with your confirmation, backing up overwritten jars first to `<game-root>/.mcmig/backups/swap/<UTC timestamp>/`. Once the migration is verified fine, these backup folders can be safely deleted.

### How to Uninstall

1. Delete the mcmig program folder (for the portable exe, `data/config.toml` — the only thing in it — goes with it, clearing the software-side state; the instance layer is step 2);
2. Optional: delete `<game-root>/.mcmig/` (snapshots / plans / rules / job journals — pure tool artifacts; re-scanning rebuilds the snapshots, and deleting the journals clears the "pending review" banner);
3. Optional: delete `_conflict_backup/` in each `<game-root>/versions/<version>/` (harmless to keep);
4. Game **content** directories themselves (mods/config/saves…) are never modified by the tool — no cleanup needed; apart from the `.mcmig/` handled in step 2, nothing else tool-related is left inside the game root.

### Verifying the Download (SHA256)

Each GitHub Release ships two artifacts plus a `SHA256SUMS.txt` checksum list. Verify after downloading (built-in Windows command):

```bat
certutil -hashfile <downloaded file> SHA256
```

Compare the output against the entry for that filename in `SHA256SUMS.txt` — a match means the download is intact; otherwise re-download. `mcmig update` performs the same verification automatically and rejects + cleans up on failure.

## Project Structure

```
mcmigrator/
├── migration/          # tool source (hashing/rules/classifier/snapshot/scanner/differ/pipeline/updater/gui)
├── tests/              # unit + end-to-end tests (pytest)
├── tools/packaging/    # release builds: build.py (both forms) / two PyInstaller specs / lockfile / release guard
├── docs/               # deferred-work ledger (backlog.md) and SDD plan records
├── Reference/          # design docs (specs / design / plans) — in Chinese
├── data/default_rules.yaml  (inside the package)  # built-in default classification rules
├── config.example.yaml # config template
├── AGENTS.md           # project conventions (for AI collaborators) — in Chinese
└── README.md
```

> Data-file hashes are generated/verified with LF normalization, so a `core.autocrlf=true` checkout
> never reports false corruption (F24); `.gitattributes` pins `migration/data/` line endings, so
> contributors need no manual line-ending conversion.

## Design & Documentation

Detailed design in `Reference/` (in Chinese): `specs/` (version design specs), `design/` (subsystem design memos), `plans/` (implementation plans).

## Contributing

For local development and running tests, install the dev dependency group: `pip install -e ".[dev]"` (pytest and ruff included). **uv users note**: `uv sync` in exact mode installs only runtime dependencies and prunes pytest — use `uv pip install -e ".[dev]"` for test environments instead.

**Building release artifacts** (requires Python 3.13; use a separate build venv and install the lockfile `tools/packaging/requirements-win-build.txt`):

```bat
python -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install -r tools\packaging\requirements-win-build.txt
.venv-build\Scripts\python.exe tools\packaging\build.py --form onefile
.venv-build\Scripts\python.exe tools\packaging\build.py --form onedir
.venv-build\Scripts\python.exe tools\packaging\build.py --sums dist-release
```

Pushing a `v*` tag runs the same pipeline in GitHub Actions (the release guard first enforces tag == pyproject == `__version__`).

Contributions welcome (in Chinese or English):

- **Classification rules** — how you categorized odd files in your modpack, e.g. `.mcmig/rules.yaml`:
  ```yaml
  rules:
    - match: "screenshots/**"
      decide: never
      reason: "player screenshots, don't migrate"
  ```
- **Whitelist entries** — player-preference files you discovered that lack `.bak` (see `migration/data/whitelist.yaml`)
- **Bug reports & feature ideas**

→ [GitHub Issues](https://github.com/Nothing-ray/mcmigrator/issues) | PRs welcome (under MIT license)

## Known Limitations

- **On legacy Chinese Windows consoles (cmd / GBK code page), emoji in reports render as `?`.** This is a limitation of the Windows console encoding (GBK/cp936), which cannot represent emoji. `mcmigrator` degrades automatically to avoid crashing — Chinese text and all paths/reasons always display correctly; only decorative symbols like ✅📦🔄 become `?`. Modern terminals (Windows Terminal / PowerShell 7) are unaffected.

- **Update assets carry same-origin integrity checks only**: `SHA256SUMS.txt` ships with the release, so it proves "asset matches the checksum list" — not the publisher's identity (no release signature yet). Always fetch artifacts from this project's Releases page; avoid third-party re-uploads. Publisher-signature evaluation is planned with the update-transaction tier.

### Encoding behavior (important)

`mcmigrator` picks the output encoding by destination:

- **Direct console display** (tty): keeps the console's native encoding (Chinese renders correctly on GBK consoles)
- **Redirected to file or pipe** (`> out.json` / consumed by other programs): **always UTF-8** (no BOM), matching the project's UTF-8 file convention — `--json` output is safe for cross-machine consumption

Two Windows environment notes (from real dedicated-server testing, 2026-09):

1. **Prefer PowerShell 7 (pwsh) or Windows Terminal**; legacy PowerShell 5.1 parses BOM-less UTF-8 scripts as GBK, and GBK consoles cannot render emoji
2. **On GBK-locale machines, prefer ASCII version names** (e.g. `pre-9.8` instead of `9.11前`) — some terminal environments mangle CJK arguments passed through bash (PowerShell passes them fine); if you hit a "snapshot missing" error, check this first

### Dedicated server scenario

A dedicated server has no `versions/` layout — **each server directory is one "version"**. Built-in default rules already cover server-core assets: `world/**`, `server.properties`, `whitelist.json`, `ops.json`, `banned-*.json` → must-migrate; `mods_*/**` (ops rollback backup dirs) → never.

World directories don't have to be named `world`: `scan` detects them dynamically (the directory named by `server.properties`'s `level-name`, plus any top-level directory containing `level.dat` — covering arbitrary names, renamed leftovers, and multi-world layouts), recording them in the snapshot's optional `world_dirs` field; scan summaries / `diff` / `plan` all inject must-migrate rules from the detection at the **lowest rule layer** (after the built-in defaults) — built-in never rules (e.g. a `.bak` inside the world) still take priority, client-side `saves/<name>/level.dat` (two levels deep) never triggers a false hit, and an empty detection leaves output byte-identical to previous versions.

A world directory kept around under a new name (world → world_backup_<date>) used to surface as a thousand-row "must-migrate on the source + target-only" false alarm; when an old path disappears from the source's `world_dirs` while a new one appears on the target's, and same-relative-subpath same-size overlap is ≥ 90%, `diff` prints a one-line suspected-world-rename hint to stderr (content didn't vanish — it moved with the renamed directory). Explanation only, no reclassification.

Zero-copy integration trick: map the server directory to `versions\<name>` with an NTFS junction (`mklink /J`), then `scan`/`diff` directly — no need to copy the 2GB+ server tree. One scan before and one after a modpack swap gives the full comparison.

## Roadmap

- ✅ v0: `scan`/`diff` read-only comparison (done)
- ✅ v1 Phase 1: `plan` subcommand + config player-edit detection (`.bak` heuristic + whitelist) (implemented)
- ✅ v1 Phase 2: `migrate` actual writes + `swap` modpack orchestration (implemented; rollback see Future)
- ✅ v0.6: transactional file operations (fsops) + portable-exe data layout + `doctor` health check + local web wizard `mcmig gui` (implemented)
- ✅ v0.12: GUI two-stage swap (preflight → install) + standalone window shell `mcmig-gui` (WebView2, falls back to browser when missing) + page refresh recovery / auto-reconnect + journal interruption banner dismiss & auto-sweep (implemented)
- ✅ v0.13: distribution loop — PyInstaller dual-form packaging + GitHub Actions auto build/release (dual-platform checks + SHA256SUMS) + basic-tier updater (`mcmig update` and the wizard's About/Update panel: check → download → verify → stage → replace manually) (implemented)
- ✅ v1.0: distribution loop complete — dual-form releases + basic-tier updater verified on real machines (official package works out of the box / read-only-directory message box / offline three-part message / real WebView2 standalone window / update staging and open-location); ready for community distribution (implemented)
- 📋 v1 Phase 3: Manifest decision persistence (auto-remember migration decisions)
- 📋 Future: Mod Profile (META-INF parsing) + content detection

See [`Reference/specs/`](Reference/specs/) for details.

## Appendix: Measured Footprint (to be filled after real-world 1.0 usage)

Size, cold-start time, and peak disk usage for onefile / onedir will be recorded after real-world 1.0 usage (informational, not an acceptance promise).

## License

MIT — see [LICENSE](LICENSE).
