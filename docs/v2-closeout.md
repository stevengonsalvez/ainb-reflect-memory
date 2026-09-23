# V2 closeout

`v2` is a second trunk, cut from `main` at `48968dd`. The six stacked context
pull requests (#42, #37, #38, #39, #40, #41) were merged onto it as one change,
#43, and closed pointing here. `main` is untouched.

The six merged without conflicts: each branch was a descendant of the one
before, so the merged tree matched the tip of the stack exactly. Everything
after that merge is review, in one commit per concern.

## What review changed

Three adversarial rounds had already run on this code. Two more ran on the
combined change, and both found defects worth the time:

Round four, on the whole diff:

- The guard that replaced `bypassPermissions` could be talked into running any
  command, through a second line, command substitution, an environment prefix,
  or brace and glob expansion.
- Raw secrets still left the machine on two paths: `reflect reindex` handed
  unredacted content to the shared store, and sharing put an unredacted title
  into the commit message, branch name and pull request.
- Drained notes were pinned to the drain's own working directory, so every one
  of them was stored unpinned and the broker served none of them.
- The broker returned entity and graph data that traced to no pinned hit, and
  its per-reason drop counts let one tenant probe another tenant's repository.
- Relabelling a note restricted left its words in merged graph descriptions,
  and a repeated `classification` key silently downgraded restricted to
  internal.
- A failing migration turned two dozen integration tests into skips while CI
  stayed green.

Round five, on the fixes themselves:

- The purge cleaned the local graph only: a note mirrored while internal and
  relabelled restricted kept its row in the shared store, pinned and
  shareable, and the broker served its full text.
- The transport fix missed the mirror behind `reflect add`, the path a user
  hits daily, because the callable it passed swallowed the pinned `sslmode`.
- A search could still read credential files, because a deny rule is judged on
  the search root, not on the files a search reads.

All of the above are fixed on `v2`, each with a test that fails without the
fix. The full suite passes, including the tier that runs against a real
Postgres.

## Open follow-ups

| what | issue | why it is not done here |
|---|---|---|
| Scope the drain writer's `Read` path | #44 | Narrowing it can wedge the queue silently, and only a live drain run would catch that |
| Per-workspace repository map and pin content hashing | #45 | A pin proves a commit and path exist, not that the note came from them; the fix changes configuration shape and needs a migration |
| The live drain proof does not run | #46 | The repository has no `COMPAT_LIVE` variable and no `ANTHROPIC_API_KEY` secret, and only the account owner can set them |

Smaller known gaps, recorded in #43 rather than as issues: the purge deletes
entities over-inclusively because nothing records which note produced one (they
return on the next reindex); Mode 1 index files are not reachable from the
purge, so both paths print the reindex to run instead; a credential glued into
a word, such as `rotates_ghp_...`, still escapes redaction, because the word
boundary is what keeps ordinary text safe; and
`tests/test_learnings_cli_floor.py` fails here and on `main` alike, from a
newer click release.

## The one thing worth saying plainly

The live drain scenarios are skipped, not passing. The drain's permission
surface is proven by unit tests and by reasoning, not by a run against the real
CLI. Setting the two repository settings in #46 is what turns that from an
argument into evidence, and it unblocks #44.
