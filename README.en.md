# mcmigrator

[中文](README.zh-CN.md) | [🏠 Landing](README.md)

> ℹ️ Community translation. The [Chinese version](README.zh-CN.md) is the authoritative source and may be ahead of this translation.
> Last synced: v0.11.0 / 2026-10-02

> A read-only scan/diff tool for Minecraft modpack version migration — compare player state across version-isolated folders (equivalent to instance isolation in MultiMC/Prism) of the same modpack.

When your modpack moves from one NeoForge version folder to another, you want to know: **which files does the player need to keep or update in the new version?** `mcmigrator` scans version folders with `scan` and compares two snapshots with `diff`, producing a migration-oriented 6-bucket report. **v0 is strictly read-only** — it never touches game files; all output lands in `.mcmig/` (layout detailed in *Data & Uninstall*), so you can run it as many times as you want.

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
| `mcmig gui [--port N] [--no-browser]` | Launch the local web migration wizard (auto-opens the browser; random free port by default) |

> `<version>` = `versions/` subfolder name (MC + loader, e.g. `1.21.1-NeoForge_21.1.227` = Minecraft 1.21.1 + NeoForge 21.1.227).

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

Create the new version folder and install its NeoForge via PCL2 first. `migrate` is resumable (re-run after an interruption continues where it left off).

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
    └── config.toml (global only)    ├── locks/ and backups/ (from Batch I W3)
```

- **Global layer (software side)**: with the portable exe it is `data/config.toml`, holding **only global config** (the game-root pointer) — nothing is ever written to AppData or user directories, and copying the whole client folder carries the config along. For source runs it is the working directory's `.mcmig/config.yaml` (unchanged). A first run with nothing configured is a welcome state (no error); the wizard's step-① input box guides you to set and persist it.
- **Instance layer (`<game-root>/.mcmig/`)**: snapshots (`snapshots/`), migration plans (`plans/`), user rules (`rules.yaml`), and job journals (`jobs/` — the migration write-ahead journal; after an abnormal exit it feeds the page's "pending review" banner) are all anchored at the game root, **same location** for both portable and source modes, so multiple modpack roots never mix. `locks/` is the cross-process instance-lock registry (reserved); `backups/` arrives with Batch I W3.
- **Migrating from the old layout**: the old portable layout `exe/data/<game-name>/snapshots|plans|rules.yaml` and the old source-mode instance state under `cwd/.mcmig/` are **read-only fallbacks** — the tool never writes to the old locations; legacy snapshots are still read, with a hint recommending a bulk move; when `rules.yaml` exists in both places, **the new location wins** (the old file is ignored with a notice). Recommended: move old `snapshots/` and `rules.yaml` into `<game-root>/.mcmig/` as a whole, then delete the old copies.

### What Gets Written on the Game Side

The only directory the tool ever creates inside the game root is `.mcmig/` (snapshots, migration plans, user rules, and job journals — pure tool artifacts; snapshots rebuild on re-scan, and deleting the job journals merely clears the "pending review" banner); no other tool directory is created. The only thing written to game **content** during migration is the **conflict backup**: a file with the same name but different content is backed up to `<target-version>/_conflict_backup/` before being overwritten (mirroring the relative path; the first backup is the pre-overwrite original, and re-runs never overwrite it). Once the migration is verified fine, that folder can be safely deleted.

### How to Uninstall

1. Delete the mcmig program folder (for the portable exe, `data/config.toml` — the only thing in it — goes with it, clearing the software-side state; the instance layer is step 2);
2. Optional: delete `<game-root>/.mcmig/` (snapshots / plans / rules / job journals — pure tool artifacts; re-scanning rebuilds the snapshots, and deleting the journals clears the "pending review" banner);
3. Optional: delete `_conflict_backup/` in each `<game-root>/versions/<version>/` (harmless to keep);
4. Game **content** directories themselves (mods/config/saves…) are never modified by the tool — no cleanup needed; apart from the `.mcmig/` handled in step 2, nothing else tool-related is left inside the game root.

### Verifying the Download (SHA256)

Each GitHub Release ships the exe plus its SHA256 checksum. Verify after downloading (built-in Windows command):

```bat
certutil -hashfile mcmig.exe SHA256
```

Compare the output against the SHA256 on the Release page — a match means the download is intact; otherwise re-download.

## Project Structure

```
mcmigrator/
├── migration/          # tool source (hashing/rules/classifier/snapshot/scanner/differ/reporter/cli)
├── tests/              # unit + end-to-end tests (pytest)
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
- 📋 v1 Phase 3: Manifest decision persistence (auto-remember migration decisions)
- 📋 Future: Mod Profile (META-INF parsing) + content detection

See [`Reference/specs/`](Reference/specs/) for details.

## License

MIT — see [LICENSE](LICENSE).
