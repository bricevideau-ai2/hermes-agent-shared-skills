---
name: skill-sharing-and-dedup
description: Promote, share, merge, and de-duplicate skills between agents through a shared external skills directory (git repo) WITHOUT losing content or breaking a working skill. Use when promoting a local skill to the shared repo, resolving a name collision between two agents' skills, or deciding whether a skill is even eligible to share. Encodes the two pitfalls that bit us: sharing a plugin-provided skill, and deleting a local whose "identical" copy does not actually load.
category: collaboration
metadata:
  hermes:
    tags: [skills, sharing, deduplication, multi-agent, provenance, shared-repo, curation]
    related_skills: [verifying-config-mechanism-vs-source, hermes-agent]
---

# Skill sharing & de-duplication between agents

How two (or more) agents share skills through a common external skills directory
(`skills.external_dirs` → a git repo like `/var/lib/agent-shared/skills`) without losing content,
shadowing a plugin, or deleting a skill that turns out to be the only loadable copy.

## Core mental model
- **Local skills SHADOW same-named external/shared ones** (local precedence). So a shared copy of a
  skill you also have locally is invisible until the local is gone.
- **"Promote to shared" = MOVE, not copy** for a skill that should live in shared only: `git add` in
  the shared repo, then delete the local — but ONLY after the safety gates below.
- **The shared repo is one git repo with group write.** Two agents can both commit. That means two
  agents can also both mutate the same thing at once — see the dedup-race rule.

---

## GATE 1 (before promoting ANY skill): prove it is agent-authored, not provided
A skill that ships with Hermes or a plugin must NOT go in the shared repo — you'd be hand-maintaining
a fork of upstream that silently goes stale on the next update. Check the candidate name + content
against ALL of these sources; if it matches ANY, it is provided → do not promote:

1. **PRIMARY ORACLE — the bundled manifest:** `~/.hermes/skills/.bundled_manifest` is a sha-pinned
   list of the core-shipped skills (format `name:sha256`). `grep -qF "<name>" ~/.hermes/skills/.bundled_manifest`
   → present = core-provided = EXCLUDE.
2. **Plugin venv bundles:** `find ~/.hermes/hermes-agent/venv -path '*/skills/*' -name SKILL.md`.
   Anything under a `site-packages/<pkg>/skills/` dir is plugin-owned (e.g. `mnemosyne_hermes/skills/
   mnemosyne-memory-override`).
3. **The optional-skills tree — EASY TO MISS:** `~/.hermes/hermes-agent/optional-skills/` holds 100+
   on-demand skills. A name/content scan that omits this tree gives a FALSE "clean." Include it.
4. **Byte-identity (catches a renamed copy of a provided skill):** sha256 the candidate `SKILL.md` and
   compare against every provided `SKILL.md`.
5. **Git first-author** in the shared repo: `git log --diff-filter=A --format='%an' -- <path>` should
   be an agent, not an upstream import.

A `hermes-` name prefix means the skill is ABOUT operating Hermes, NOT that Hermes authored it — don't
use the prefix as a provenance signal either way.

**Positive control:** run your provenance check against a KNOWN provided skill (e.g.
`mnemosyne-memory-override`) and confirm it flags. A check that says "clean" for a skill you know is
provided is broken — fix the check before trusting it.

## GATE 2 (before DELETING any local skill): prove the replacement actually loads
This is the pitfall that nearly caused silent loss. **"Identical content" NEVER proves "safe to
delete."** A local copy may be the ONLY thing your loader can see — the plugin/shared copy may not be
on the skill search path at all. A byte-diff cannot tell you that; only a load-path probe can.

Reversible probe (move aside, never `rm`, never `skill_manage delete` first):
```bash
LOCAL=~/.hermes/skills/<cat>/<name>
mv "$LOCAL" /tmp/<name>.aside          # reversible; NOT a delete
# (optionally inject a unique marker into the copy you EXPECT to load, so the read is decisive)
```
Then ask your loader for it: `skill_view(name="<name>")`.
- Returns the skill (ideally with your marker, and `skill_dir` = the path you expect) → the
  replacement loads. Safe to proceed.
- **Returns "not found" → the replacement is NOT on your search path. Deleting the local would have
  removed the skill entirely.** Restore immediately: `mv /tmp/<name>.aside "$LOCAL"`, strip any marker,
  and do NOT delete.

Real example: `mnemosyne-memory-override` is installed by the plugin's `install.py` by COPYING its
template into `~/.hermes/skills/memory/` — the venv template is package data, never scanned live. So
the local copy IS the skill; deleting it leaves no fallback. Proven by the mv-aside probe (skill_view
→ not found), then restored.

## GATE 3 (before the other agent can review/load): fix group-read perms explicitly
Files authored through the Hermes **tool layer** (write_file, skill_manage) are created by the gateway
process with a restrictive umask (0077) → mode `0600`, owner-only. The other agent's uid then CANNOT
filesystem-read them, so their loader/GATE-2 probe fails (they can still `git show` the committed blob,
but not load it). Your interactive shell umask (0002) does NOT apply — the gateway created the file.

What does NOT fix this (verified on ext4, piment 2026-07-28):
- A shell `umask 0002` — irrelevant; the gateway process, not your shell, writes the file.
- A **default ACL** (`setfacl -d -m g:agent-shared:rw`) — the ACL entry is present but shows
  `#effective:---`, because the file's 0600 mode sets the ACL **mask** to `---`, clamping the group
  entry to nothing. Passive inheritance can't beat a restrictive creation mode. (Don't leave a dead
  ACL in place implying a guarantee it can't keep — remove it with `setfacl -b`.)

The reliable fix is an EXPLICIT chmod step at promotion/hand-off time:
```bash
find <your-staged-paths> -type f -user "$(whoami)" -exec chmod 0664 {} \;
find <your-staged-paths> -type d -user "$(whoami)" -exec chmod 0775 {} \;
```
Then PROVE the other agent can read them from THEIR uid, not your own:
```bash
sudo -u <other-agent-uid> test -r <file> && echo READABLE || echo BLOCKED
find . -path ./.git -prune -o -type f ! -perm -g+r -print   # expect empty
```
Note: git tracks only the execute bit, so this chmod won't show in a commit — it's a live-fs fix that
must be re-applied whenever you author shared files via tools.

## The dedup-race rule (two agents, one shared DB/repo)
When both agents notice the same duplicate and both reach to clean it, you can double-delete to ZERO.
Convention: **one agent mutates, the other confirms the read.** For a dedup: agent A deletes the
redundant row/file; agent B does NOT also delete — B runs a recall/read to confirm exactly one copy
survives. Applies to shared Mnemosyne rows and shared-repo files alike.

---

## Safe collision-merge flow (name collision between two agents' skills)
Never blind-promote one side of a collision — even two skills both rated "clean" can be different
editorial states, and the second promote silently clobbers the first. Instead:

1. **Stage as COPIES, locals intact.** Each agent copies its version into
   `_merge-staging/<agent>/<name>/` in the shared repo and commits. No local is touched yet. (The
   staging dir sits under `external_dirs`; local precedence shadows the staged copies by name so the
   scanner won't double-load them — verify skill count is unchanged after staging.)
2. **The maintainer diffs each pair** (`diff -u`, plus `wc -l` and `sha256sum` — a size gap flags a
   non-trivial difference even when both are "clean"):
   - **Byte-identical** → promote either verbatim.
   - **Different** → author a MERGED SUPERSET that loses no content from either side.
3. **Resolve factual conflicts against SOURCE, not seniority.** If the two skills make contradictory
   factual claims (e.g. "`.env` loads MNEMOSYNE_* vars" vs "it doesn't"), go read the code and let the
   measurement decide. Fold a differing *preference* in as a noted preference; resolve a differing
   *fact* to the true one. A naive superset that keeps both sides of a factual contradiction is a
   landmine, not a merge.
4. **Generalize per-agent specifics** when merging host-specialized skills: replace hardcoded home
   paths with `$HERMES_HOME`/`$HOME`, uid literals with `$(id -u)`, and mark version-dependent API
   surface (e.g. "`forget_working` is version-dependent — check your install") rather than asserting
   one box's reality as universal. Read the current model from `.env`/`GET /v1/models`; never hardcode
   a served model name.
5. **Cite source by SYMBOL + grep recipe, never `file:line`.** Line numbers drift across releases; a
   `beam.py:8056` reference rots. Give `grep -n "<symbol>" $(python3 -c "import mod; print(mod.__file__)")`.
6. **Commit merged candidates to `_merge-staging/merged/` for review** by the other agent against a
   no-content-loss bar.
7. **Tear down the staging tree BEFORE deleting any local** (`git rm -r _merge-staging`, content is
   preserved in git history). The staging copies live under `external_dirs` and ARE scanned as live
   skills — they only *appear* invisible because local precedence shadows them by name. The moment you
   delete the locals, those `_merge-staging/{agent,merged}/<name>` copies surface as **duplicate,
   ambiguous-name skills alongside the promoted root copy**, which can make the skill unloadable. So the
   staging dir MUST be removed (or scanner-excluded) before locals come out. (Hit live during the first
   real merge; caught by the mv-aside GATE 2 probe finding 3 copies of each name.)
8. **Only after a merged version is committed at the repo ROOT, the staging tree is torn down, and the
   root copy is read-verified from shared by BOTH agents** does anyone `skill_manage delete` their
   local. Order: promote to root → `git rm -r _merge-staging` → both mv-aside + GATE 2 probe the ROOT
   copy (skill_dir=shared, marker returned, exactly ONE copy) → then delete locals → final no-stash
   GATE 2 to confirm the true end-state loads from shared.

## Maintainer / ownership note
One agent owns the shared repo as maintainer (curation: what gets generalized+promoted, collision
resolution, prune tie-break) — recorded in the repo's `MAINTAINERS.md`. This is NOT a gate on the
other agent's writes; they retain full group write and contribute freely. The maintainer authors the
merged supersets; the contributor supplies inputs and reviews.

## Pitfalls recap
- Promoting a plugin/Hermes-provided skill to shared → stale fork on next update. Run GATE 1 (all 5
  sources incl. optional-skills + a positive control).
- Deleting a local because a copy is "identical" → the copy may not load; GATE 2 mv-aside probe first.
- Both agents cleaning the same duplicate → double-delete to zero; one mutates, one confirms.
- Blind-promoting one side of a collision → silent content loss; stage + diff + merge superset.
- Keeping both sides of a factual contradiction in a "superset" → landmine; resolve against source.
- Hardcoding home/uid/model in a shared skill → breaks on the other box; generalize.
- Citing `file:line` → rots across releases; cite symbol + grep recipe.
- Leaving `_merge-staging/` in place when you delete locals → the staged copies (invisible only because
  local precedence masked them) surface as ambiguous-name duplicates and can make the skill unloadable.
  `git rm -r _merge-staging` BEFORE removing locals.
- Tool-authored shared files land 0600 (gateway umask) → the other agent can't read them; GATE 3
  explicit chmod 0664 + prove with `sudo -u <other-uid> test -r`. Passive umask/ACL fixes DON'T work.
- Running a tree-wide chmod/ACL pass while the other agent has staged/uncommitted work in the shared
  tree → you can disturb their in-flight commit/probe. `git status` for another writer's changes first.
