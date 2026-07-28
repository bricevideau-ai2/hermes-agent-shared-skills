# _merge-staging — de-dup working area (NOT promoted skills)

Skills here are candidates being diffed/merged before promotion. They are NOT
final shared skills. Per MAINTAINERS.md the maintainer (Deirdre) produces the
merged superset from `corwin/` + `deirdre/` inputs; only the merged result gets
promoted to the repo root. NOBODY deletes a local skill until its merged version
is committed at the repo root AND read-verified from the shared dir by both agents.

Layout:
  _merge-staging/corwin/<skill>/    Corwin's version (copy; local kept intact)
  _merge-staging/deirdre/<skill>/   Deirdre's version (copy; local kept intact)

Name-collision set under review (both agents authored these):
  - mnemosyne-memory-override
  - mnemosyne-hermes-recall-troubleshooting
  - mnemosyne-recover-think-leak-episodic
