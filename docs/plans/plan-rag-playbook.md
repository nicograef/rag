# Plan: RAG Playbook Documentation Rework

> Source PRD: [../prds/prd-rag-playbook.md](../prds/prd-rag-playbook.md)

> **Completed (2026-07-11):** the documentation rework landed — kept as the historical plan.

## Goal

Reposition the repository as a **RAG playbook** — a production-shaped, self-hosted,
framework-free reference implementation a learner can clone and run — by reworking the
documentation only: contributor/agent instructions, roadmap presentation, and the README
front door. No pipeline code changes; pipeline phases continue per the roadmap afterwards,
under the new definition of done.

## Architectural decisions

Durable decisions that apply across all phases:

- **Documentation taxonomy** (who reads what):
  - `README.md` — learner front door (audience, status table, quick start with first-run
    costs, pipeline overview, support policy).
  - `AGENTS.md` (loaded via `CLAUDE.md`) — contributor/agent instructions; never leaks
    into reader docs.
  - `docs/roadmap.md` — living execution plan and the dated decision log; single source
    of truth for decisions, other docs link to it.
  - `docs/prds/prd-rag-playbook.md` — product big picture; stable unless a decision
    amends it.
  - `docs/theory/<building-block>.md` — one concise theory chapter per building block;
    the directory is created when the first chapter lands (Phase 1 of the roadmap), not
    in this rework.
- **Stage taxonomy** (names used identically everywhere): offline **fetch → convert →
  chunk → embed → load**; online **retrieve → assemble → generate**; **evaluate** is a
  cross-cutting harness, not a stage.
- **Definition of done** for every future roadmap phase: code + tests + theory chapter +
  documented stage contract + updated README status (with verification date). Stated in
  full **once, in `AGENTS.md`**; the roadmap's decision entry links to it.
- **Learner-facing identity**: README H1 becomes **"RAG Playbook"**; the GitHub repo name
  stays `rag`. Self-description everywhere: a learning project that doubles as a public
  playbook — in that order.
- **Honesty gates**: the README status table gates every runnable-experience claim;
  time-sensitive claims carry the date they were last verified; the playbook never claims
  "state of the art".

## Inventory

Relevant existing files (the repo is a Phase-0 scaffold — no pipeline code exists yet):

- `README.md — "Quick start", "Structure", "Conventions", "License"` — current front door;
  written from the maintainer's context, no audience statement, no status table, no
  support policy. Rewritten in Phase 3.
- `AGENTS.md — intro, "Rules", "Learning", "Boundaries", "Quality Principles"` — canonical
  agent instruction set, currently framed as a private learning project. Updated in
  Phase 1.
- `CLAUDE.md — "Claude Code", "Non-negotiables"` — loads `AGENTS.md` via `@AGENTS.md`;
  content is session mechanics only, expected to need no positioning change (verified in
  Phase 1).
- `docs/roadmap.md — "Phase 0"–"Phase 5+", "Decisions"` — phased plan and dated decision
  log; annotated in Phase 2, phase count/ordering/content unchanged.
- `docs/prds/prd-rag-playbook.md` — the source PRD; not modified by this plan.
- `tests/test_smoke.py — test_package_importable()` — prior art for testing through the
  public package surface; referenced by the DoD wording, not changed.
- `Makefile — check target` — `make check` (lint + types + tests) is the verification
  command each phase runs before completion.

## Resolved decisions

- DoD lives in full in `AGENTS.md`; roadmap decision entry links to it (user-confirmed).
- Theory chapters: `docs/theory/<building-block>.md`, directory created when the first
  chapter lands — no speculative empty structure now (user-confirmed).
- README H1 becomes "RAG Playbook"; repo name unchanged (user-confirmed).
- **Verification rule strengthened (user request):** the `AGENTS.md` update in Phase 1
  broadens the existing "Web search for external knowledge" boundary so that all agents
  working in this repo always verify trained knowledge against current authoritative
  public sources (official docs, model cards, papers, primary sources) before relying on
  it — to prevent hallucinated, outdated, or assumed claims — and date every
  time-sensitive claim. This also implements the PRD's "honest currency" pillar.
- Stage contracts are documented per-phase when each stage lands; this rework documents
  only the taxonomy and the contract *convention* (offline: input/output artifacts;
  online: entry point + step-level logs). No `docs/` contract files are created now.
- Quick start stays honest to Phase 0: dev setup + database only. First-run costs section
  states today's real costs (uv tooling, Postgres container image) and names the future
  multi-gigabyte model downloads as a planned cost, gated by the status table.
- Licensing claim (§ 5 UrhG, amtliche Werke) keeps its existing wording; verification
  against the source's terms of use stays deferred to the roadmap's fetch phase, per PRD.
- No docs site, no link-checker tooling; link validity is a manual review-checklist item
  per phase (PRD "Testing Decisions").

## Open questions / Risks

- `AGENTS.md` is duplicated into this session's context by tooling, but the repo file is
  the only source; no drift risk inside the repo itself.
- The README rewrite must not promise anything the status table doesn't cover — the
  self-review step of each phase re-reads for accidental runnable-experience claims.

---

## Phase 1: Contributor/agent instructions — new positioning, DoD, verification rule

**User stories**: 8, 9, 10 (maintainer framing), 11 (DoD), plus the user's verification
request.

### Context

- `AGENTS.md — intro paragraph` — currently "A learning project … the point of this repo
  is understanding every moving part"; gains the playbook framing (learning project first,
  public playbook second).
- `AGENTS.md — "Rules"` — rule 5 (phase-by-phase) is where the DoD attaches naturally.
- `AGENTS.md — "Boundaries"` — contains the "Web search for external knowledge" boundary
  to strengthen.
- `AGENTS.md — "Learning"` — "Explain while building" becomes the theory-chapter
  convention (`docs/theory/<building-block>.md`, written in the same phase as the code,
  cross-linked both ways).
- `CLAUDE.md` — re-read to confirm no positioning language needs updating (expected: none).

### What to build

Update the agent instruction set to the playbook positioning without leaking any
reader-facing prose into it:

1. Intro: reposition as "a learning project that doubles as a public RAG playbook — in
   that order"; state explicitly it is not a product, hosted service, or supported
   product; name the full stage taxonomy (offline fetch → convert → chunk → embed → load;
   online retrieve → assemble → generate; evaluate as cross-cutting harness).
2. Definition of done: add the rule, stated in full — every future roadmap phase lands
   code + tests + theory chapter + documented stage contract + updated README status with
   verification date.
3. Theory convention in "Learning": chapters live at `docs/theory/<building-block>.md`,
   written in-phase, cross-linked from module docstrings and the README pipeline overview;
   a concept is explained exactly once.
4. Verification boundary: broaden "Web search for external knowledge" to "always verify
   trained knowledge against current authoritative public sources (official docs, model
   cards, papers, primary sources); never assert time-sensitive facts from memory; date
   time-sensitive claims".

### Acceptance criteria

- [x] `AGENTS.md` intro describes the repo as a learning project that doubles as a public
      playbook, names what it is explicitly not, and uses the exact stage taxonomy names
      from this plan's architectural decisions.
- [x] The definition of done appears once, in full, in `AGENTS.md`, and nowhere else in
      the repo except as links.
- [x] The strengthened verification boundary covers trained knowledge (not only external
      tools) and requires dating time-sensitive claims.
- [x] The theory-chapter convention names `docs/theory/<building-block>.md` and the
      two-way cross-linking rule; no `docs/theory/` directory is created.
- [x] `CLAUDE.md` re-read; updated only if positioning language is found (expected: no
      change).
- [x] No reader-facing (learner) prose added to `AGENTS.md`/`CLAUDE.md`; `make check`
      passes.

---

## Phase 2: Roadmap — stage annotations and repositioning decision entry

**User stories**: 3 (stage = module made visible in the plan), 6 (dated decisions).

### Context

- `docs/roadmap.md — "Phase 1"–"Phase 5+"` — each phase gains an annotation naming the
  stage(s) it lands; phase count, ordering, and content stay unchanged (PRD: presentation
  only).
- `docs/roadmap.md — "Decisions"` — the dated decision log where the repositioning entry
  is appended.

### What to build

Annotate and record — no reorganization:

1. Stage annotations, using the taxonomy names verbatim: Phase 1 → **fetch**, **convert**;
   Phase 2 → **chunk**; Phase 3 → **embed**, **load**; Phase 4 → **retrieve**,
   **assemble**, **generate**; Phase 5+ backlog intro notes that a backlog phase may add
   or split a stage by amending the PRD's taxonomy in the same change, and that the
   evaluation item lands the cross-cutting **evaluate** harness.
2. A dated decision entry (full block, like the Docker entry): the playbook
   repositioning — context, choice, consequences — linking to the PRD for the big picture
   and to `AGENTS.md` for the definition of done, without restating either.

### Acceptance criteria

- [x] Every roadmap phase (1–5+) names the stage(s) it lands, using the exact taxonomy
      names; diff shows annotations and the decision entry only — no phase reordered,
      removed, or reworded beyond the annotation.
- [x] The decision entry is dated, follows the existing full-block convention, and links
      to the PRD and to the `AGENTS.md` DoD instead of restating them.
- [x] All links in the changed file resolve to existing files/sections; `make check`
      passes.

---

## Phase 3: README rewrite — the learner front door

**User stories**: 1, 2 (as gated promises), 4 (swap-path pointer), 5 (theory pointer),
7 (front door), 10 (support policy).

### Context

- `README.md` — full rewrite; current sections ("Quick start", "Structure",
  "Conventions", "License") are absorbed into the new structure.
- `docs/roadmap.md — "Phase 0"–"Phase 5+"` (after Phase 2) — source for the status table
  rows and stage annotations.
- `docs/prds/prd-rag-playbook.md — "Solution", "Implementation Decisions"` — source for
  audience statement, pillars, constraints-as-features, and support-policy wording.

### What to build

Rewrite `README.md` as the learner front door, in this order:

1. **H1 "RAG Playbook"** + audience statement: what this is (production-shaped,
   self-hosted, framework-free reference implementation of RAG over German federal law,
   for developers learning RAG hands-on), what it is not (product, hosted service,
   supported software, state-of-the-art claim), and the constraints elevated to features
   (open-source only, CPU-only 8-core/16 GB design floor, no RAG frameworks, real
   public-domain corpus, re-runnable from clean checkout, no data in git).
2. **Status table**: one row per roadmap phase with its stage annotation(s) and status
   (today: Phase 0 ✅, all else ⬜), plus the last quick-start verification date. Every
   runnable-experience claim in the file is phrased against this table.
3. **Quick start** with first-run costs: today's commands (dev tools, `.env`, `make db`,
   `make check`) and their real costs; a clearly gated note that future phases add
   multi-gigabyte model downloads (embedding model, LLM weights), documented when those
   phases land.
4. **Pipeline overview**: the stage taxonomy as a table — stage, responsibility,
   input → output artifact (offline) or entry point + step logs (online) — with evaluate
   described as a cross-cutting harness; notes that each stage's contract and theory
   chapter (`docs/theory/`) land with its phase; the data flow is the table of contents.
5. **Corpus and swap path**: German federal law as a feature (real structure, § 5 UrhG
   public domain, existing licensing wording kept), the German-language limitation named,
   and the honest swap blast radius: reimplement fetch + convert, adapt chunker structure
   and citation fields; contracts state exactly which fields downstream stages require.
6. **Project status & support policy**: grows chapter by chapter, explicitly incomplete;
   maintainer sets pacing; no support, no SLA, no contribution program; external
   dependencies can rot between phases.
7. **Structure table, conventions pointer, license** — updated to include `docs/prds/`
   and `docs/plans/`; contributor/agent material stays a one-line pointer to `AGENTS.md`.

### Acceptance criteria

- [x] README states audience, landed-vs-planned status table (matching
      `docs/roadmap.md` exactly), quick start with today's first-run costs and the gated
      future-downloads note, pipeline overview with all eight stage names + evaluate,
      corpus swap path with its blast radius, and the project-status/no-support
      statement.
- [x] No claim in the README promises a runnable experience beyond Phase 0; no
      "state of the art" claim anywhere; time-sensitive statements carry a date.
- [x] Reader-facing prose contains no contributor/agent workflow rules — those remain
      only a link to `AGENTS.md`.
- [x] Every relative link in the README resolves; the status table's stage names match
      the taxonomy verbatim; `make check` passes.
- [x] Quick start re-verified from a clean checkout (2026-07-11) and the date recorded
      next to the status table; `make db` verified up to the image pull only — the
      verifying sandbox's network blocked Docker Hub blob downloads (disclosed in the
      README next to the date).
