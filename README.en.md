# mcmigrator

[中文](README.zh-CN.md) | [🏠 Landing](README.md)

> ℹ️ Community translation. The [Chinese version](README.zh-CN.md) is the authoritative source and may be ahead of this translation.
> Last synced: v0.1.0 / 2026-07-07

> A read-only scan/diff tool for Minecraft modpack version migration — compare player state across version-isolated folders (equivalent to instance isolation in MultiMC/Prism) of the same modpack.

When your modpack moves from one NeoForge version folder to another, you want to know: **which files does the player need to keep or update in the new version?** `mcmigrator` scans version folders with `scan` and compares two snapshots with `diff`, producing a migration-oriented 6-bucket report. **v0 is strictly read-only** — it never writes to the game directory; all output lands in the working directory's `.mcmig/`, so you can run it as many times as you want.

## Features

- **Tiered hashing**: full MD5 for text, filename-set for mods, size proxy for bulk (`.sqlite`/`.zip`/`.mca`) — fast and precise (byte-level for text the player edits; size proxy for binaries they don't).
- **Data-driven classification**: rule engine (`pathspec`, gitignore semantics), layered first-match-wins (CLI override > user rules > built-in default > unknown); changing rules doesn't require rescanning.
- **Migration-oriented 6-bucket diff**: `to_migrate` / `candidate` / `mods` (by filename set) / `only_in_dst` / `identical` / `never`.
- **Zero writes**: read-only on the game directory; rollback/repeated experiments are inherently safe (game state is immutable).

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
3. **Config file**: `cp config.example.yaml .mcmig/config.yaml`, edit `game_root` in it

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

> `<version>` = `versions/` subfolder name (MC + loader, e.g. `1.21.1-NeoForge_21.1.227` = Minecraft 1.21.1 + NeoForge 21.1.227).

## How It Works

1. `scan` traverses the version folder, hashes by the tiered strategy, and produces a **raw manifest snapshot** (`.mcmig/snapshots/<ver>.snapshot.json`, **no classification**).
2. `diff` reads two snapshots, **classifies by current rules on the fly**, and assigns each file to one of 6 buckets.
3. After changing rules (user `.mcmig/rules.yaml` or CLI `--exclude`/`--include`), **re-run `diff` without rescanning** — classification is computed at snapshot-read time.

### Tiered Hashing

| File type | Basis | Reason |
|---|---|---|
| Text (`config/`, `options.txt`, `*.dat`, scripts) | Full MD5 | Players edit these; need byte-level precision |
| `mods/**/*.jar` | Filename set | Players don't edit jar internals; version change = filename change |
| `*.sqlite` / `*.zip` / `*.mca` | size | Bulk-replace type; size is a good proxy |

`--strict` forces full hashing as an escape hatch.

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

## Design & Documentation

Detailed design in `Reference/` (in Chinese): `specs/` (version design specs), `design/` (subsystem design memos), `plans/` (implementation plans).

## Contributing

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

## Roadmap

- ✅ v0: `scan`/`diff` read-only comparison (done)
- 🚧 v1 Phase 1: `plan` subcommand + config player-edit detection (`.bak` heuristic + whitelist) (in design)
- 📋 v1 Phase 2: `migrate` actual writes + rollback
- 📋 v1 Phase 3: Manifest decision persistence (auto-remember migration decisions)
- 📋 Future: Mod Profile (META-INF parsing) + content detection + GUI

See [`Reference/specs/`](Reference/specs/) for details.

## License

MIT — see [LICENSE](LICENSE).
