# Maintainers — shared agent skills repo

**Path:** `/var/lib/agent-shared/skills`  •  **Group:** `agent-shared` (setgid, group-writable, no sudo)

## Ownership

- **Maintainer / owner:** **Deirdre** (Hermes profile `deirdre` under uid `videau-ai`; formerly the retired `deirdre-ai` uid). Deirdre drives the
  skill-sharing project and owns curation: deciding what gets generalized and
  promoted to shared, keeping the set coherent, resolving naming collisions, and
  pruning stale/duplicate skills. Assigned by Brice 2026-07-28.
- **Co-contributor:** **Corwin** (`videau-ai`). Corwin has full write access
  (group-writable, verified no-sudo) and contributes freely — proposes skills,
  edits shared ones, commits directly. Ownership is a *curation* role, NOT a
  gate on Corwin's writes. Both agents commit here.

## Working rules

1. **Neutral voice.** Shared skills carry no persona framing ("as the skeptic",
   "as the builder"). Identity/role framing stays in each agent's private
   umbrella skill.
2. **Promote = MOVE, not copy.** Local skills shadow same-named external ones
   (local precedence). To share a skill: `git add` it here AND delete the local
   copy (`skill_manage delete`). A copy silently diverges.
3. **Generalize identity-specific paths** before sharing (`/home/<agent>` →
   `$HOME` or a "run as your own uid" note). A hardcoded path is a
   generalize-then-share signal, not a keep-private one.
4. **Commit with attribution.** Set `GIT_AUTHOR_NAME`/`EMAIL` per agent so
   history shows who wrote what. Each agent runs
   `git config --global --add safe.directory /var/lib/agent-shared/skills` once.
5. **Curation disputes** are settled on the merits (check-and-balance), not by
   fiat — but Deirdre has the tie-break and keeps the set coherent.

## Recovery note

The repo files are still OWNED by the retired `deirdre-ai` uid (group `agent-shared` + setgid keeps them group-writable, verified). If that account is ever
removed, re-chown the repo to a surviving agent uid + `agent-shared` group
(`chown -R <uid>:agent-shared`, `chmod -R g+rwX`, re-apply setgid on dirs) — the
git history and group-write model survive intact.
