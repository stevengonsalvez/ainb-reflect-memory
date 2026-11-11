# Rename migration: plugly-reflect-memory

The product, Python distribution, executable, native plugin and marketplace use
`plugly-reflect-memory`. Existing `reflect` executable calls remain supported.
Python imports stay `reflect_kb`; canonical imports use `plugly_reflect_memory`.

## Compatibility contracts

Existing installs retain their memory and runtime state. Do not move or delete
`~/.reflect`, `~/.learnings`, `~/.claude/global-learnings`, user-selected KB paths,
`REFLECT_*` variables, `GLOBAL_LEARNINGS_PATH`, `reflect.toml`, Postgres tables,
queue files, locks or persisted event identifiers during rename. Legacy launchd labels remain active until explicitly replaced with canonical templates.
These are storage and integration contracts, not product branding.

The `reflect-kb-frontmatter` pre-commit ID and `reflect-kb` Nix output remain
aliases. Existing hook script filenames and the statusline timeline path remain
valid. Historical design records, benchmark reports and released versions retain
the names used when written. Browser storage keys and HTTP headers stay compatible.

## Existing machine transition

1. Save existing package source and version, enabled plugin identities and adapter
   settings. Back up hook configuration before changing registration.
2. Stop scheduling old capture hooks before enabling new registration. Let any
   active drain finish; never remove its lock or queue to force migration.
3. Uninstall the old Python tool distribution (`uv tool uninstall reflect-kb`)
   before installing the new one. Both distributions own the `reflect` alias,
   so do not install both into one tool directory. Reinstall from the prior
   source/version to roll back if the replacement fails.
4. Install the canonical distribution from the renamed repository:

   ```bash
   uv tool install --upgrade --torch-backend cpu \
     'git+https://github.com/stevengonsalvez/plugly-reflect-memory.git[graph]'
   plugly-reflect-memory --version
   reflect --version
   ```

5. Native Claude users remove old `reflect@ainb-reflect-memory` registration,
   then add the new marketplace and plugin:

   ```bash
   claude plugin uninstall reflect@ainb-reflect-memory
   claude plugin marketplace add stevengonsalvez/plugly-reflect-memory
   claude plugin install plugly-reflect-memory@plugly-reflect-memory
   ```

   Account for other installed marketplace identities, such as ai-coder-rules,
   before enabling the replacement. Only one identity should own capture hooks.
6. Inspect the machine before changing adapter registration:

   ```bash
   plugly-reflect-memory migrate inspect
   ```

   The packaged `migrate apply` command supports Codex, Copilot and Hermes.
   For example, Codex users run:

   ```bash
   plugly-reflect-memory migrate apply --harness codex
   ```

   Snapshots and transaction journals live under `~/.reflect/rename-migrations`.
   Repeat apply is safe; rollback refuses to overwrite later edits. Use the
   returned transaction identifier with `migrate rollback` to restore a saved
   transaction. Only adapter skills and hook settings are migrated. Claude
   registry entries, launchd jobs and git wrappers remain operator-managed.
   Unrelated user hooks remain intact. Native Codex and APM
   users explicitly remove their previous package registration before installing
   the canonical identity. Do not combine adapter and native hooks for one harness.
7. APM package output is `build/apm/plugly-reflect-memory`; the old build marker
   remains recognized when rebuilding an owned legacy output directory.
8. Restart harness sessions and verify one SessionStart recall, one Stop enqueue,
   one drain, one ingest, and unchanged note counts. Check errors and statusline.

## Repository and release cutover

Code changes alone do not rename the GitHub repository or deploy its Pages site.
Before publishing a migration release:

1. Rename `stevengonsalvez/ainb-reflect-memory` to
   `stevengonsalvez/plugly-reflect-memory`; update local remotes and repository
   homepage. Confirm old clone URLs and new URLs resolve as intended.
2. Release plugin `6.0.0` across all four plugin manifests and engine `0.4.0`
   after migration acceptance passes. These remain separate version streams.
3. Review downstream `ai-coder-rules` marketplace changes. The release workflow
   accepts exactly one old or new plugin entry, updates its canonical name, source
   repository and tag, and records a `renames` mapping. Duplicate entries fail.
   It does not rewrite third-party installs or migrate already enabled hooks.
4. Deploy docs at `/plugly-reflect-memory/`. Old GitHub Pages URLs require a
   separately verified redirect strategy; repository URL redirects alone do not
   establish Pages continuity. Keep old published links working until transition.
5. Update agents-in-a-box bootstrap, external statusline/plugin lookup code,
   pinned marketplace consumers, bookmarks and installation automation.
   Those repositories and installed copies are outside this change.
6. Confirm any public package registry name availability before publishing there.
   This change does not publish a registry package, release, site or marketplace.

## Rollback and acceptance

Disable new hook registration before restoring old registration. Reinstall the
previous package source/version and restore backed-up adapter settings. Because
state paths and schemas stay stable, rollback needs no data conversion.

Validate canonical and alias commands against the same KB; install, reinstall
and uninstall adapters in disposable homes; preserve unrelated hook entries;
prove one enqueue and one drain across old/new transition; build docs and check
links; build APM and run install tests; check every manifest and packaged asset.

## Canonical scheduler templates

The five `plugin/launchd/com.plugly-reflect-memory.*.plist` templates provide canonical job labels. Legacy `com.reflect.*` templates remain compatibility inputs. On macOS, unload each exact old label before loading its rendered replacement; never enable both sets. The canonical maintenance template checks canonical drain and maintenance labels. Linux validation parses templates but does not establish launchd runtime behavior.

Rollback snapshots are integrity checked before any live path is replaced. Interrupted rollback remains journaled and can be retried with the same transaction ID. Close harness sessions before applying or restoring. Snapshots can contain private skill/configuration content and remain in a private directory; do not commit or upload them.
