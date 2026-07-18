# PRD: RAG Playbook

The product-level big picture for this repository. The execution plan (phases, backlog,
dated decisions) lives in [../roadmap.md](../roadmap.md); this document states what the
repo is, for whom, and what "good" looks like. The deliverable under this PRD is the
documentation rework that executes the repositioning; the pipeline itself remains governed
by the roadmap.

## Problem Statement

People who want to learn how RAG actually works have two bad options. Tutorials are
toy-grade: fixed-size chunking on a blog post, a cloud embedding API, no evaluation, no
production concerns. Framework codebases (LangChain, LlamaIndex, Haystack) are
production-grade but hide every moving part behind abstractions — reading them teaches the
framework, not RAG. There is no self-contained, framework-free reference a developer can
clone and run end to end on an ordinary dev machine, with the theory explained next to the
code that implements it.

This repository's constraints already match that gap — open-source only, CPU-only, no
frameworks, a real, properly-licensed corpus — but it is currently a scaffold whose documentation
addresses one person. The README describes the project and its dev commands, but from the
maintainer's context: no audience statement, no landed-versus-planned status, no stage
contracts, no theory, no support policy. The big picture exists only implicitly in the
roadmap; there is no product-level statement of audience, promises, and non-promises.

## Solution

Reposition the repository as a **RAG playbook**: a production-shaped, self-hosted,
framework-free reference implementation of a complete RAG system over a real corpus
(English Wikipedia — the 20 current Premier League clubs), that a learner can clone and
run on their own machine.

The playbook rests on four pillars:

1. **Small modules with clear contracts.** The offline pipeline is sliced into
   single-responsibility stages, each with documented input and output artifacts (files on
   disk or database state), runnable on its own, its output inspectable before the next
   stage consumes it. The online path runs as one process per question; its stages promise
   a different contract — documented entry points plus step-level logging of every
   intermediate (query, retrieved chunks with scores, assembled prompt) — so the flow
   stays as inspectable as the offline artifacts. The data flow is the table of contents.
2. **Theory next to code.** Every building block (chunking, embeddings, vector indexes,
   hybrid search, reranking, evaluation, …) gets a concise theory chapter, written in the
   same increment as the code, cross-linked in both directions. The repo is self-contained:
   a learner needs no external course to follow it.
3. **Honest currency instead of "state of the art".** RAG fashion rots in months. The
   playbook teaches durable building blocks and records its concrete choices (models,
   parameters, trade-offs) as dated decisions a reader can re-evaluate. When a choice is
   superseded, its decision is rewritten in place rather than archived beside the
   replacement, so the docs state what holds now — the playbook never claims to be the
   current best, and never reads as a museum of what it used to be.
4. **Grows chapter by chapter.** The playbook is explicitly incomplete and says so. Each
   landed phase is complete on its own — code, tests, theory chapter, runnable stage. The
   README states landed versus planned truthfully, and every runnable-experience claim is
   gated on that status. Pacing stays with the maintainer; this is still a learning
   project first.

The target learner experience, once the roadmap's MVP phases have landed: clone, run a few
documented commands (the first run downloads several gigabytes of model weights —
documented up front), watch a real corpus flow stage by stage into a database, ask a
question in the terminal, and get a grounded answer with citations — and at every stage,
read why it works the way it does. Until then, the README status table says exactly which
chapters exist today, and the documented stage contracts state what swapping in your own
corpus actually requires.

## User Stories

1. As a learner, I want to clone the repo and run every landed pipeline stage on my own
   dev machine with a few documented commands, so that I can work through a real RAG
   system without cloud accounts, paid APIs, or a GPU.
2. As a learner, I want to ask a question in the terminal and get a grounded answer with
   citations once the online phases have landed, so that I experience the complete RAG
   loop the playbook builds toward.
3. As a learner, I want each pipeline stage to be a small single-responsibility module
   with a documented contract, so that I can run, inspect, and understand one stage at a
   time instead of debugging a monolith.
4. As a learner, I want the stage contracts to state exactly which fields and artifacts
   downstream stages require, so that I know precisely which stages to reimplement or
   adapt when I swap in my own corpus.
5. As a learner, I want a concise theory chapter per building block, cross-linked with the
   code that implements it, so that I understand why a technique exists before I read how
   it is implemented.
6. As a learner, I want every concrete choice (embedding model, chunk parameters, index
   type, LLM) recorded as a dated decision with reasoning, so that I can judge what is
   still current and adapt it.
7. As a learner, I want a front-door README that states what the playbook covers, what is
   landed versus planned, what the first run costs (downloads, disk, RAM), and how to
   start, so that I can orient myself in minutes.
8. As the maintainer, I want the playbook framed as growing chapter by chapter, so that
   the public repo stays honest while incomplete and each landed phase stands alone.
9. As the maintainer, I want reader-facing documentation separated from contributor- and
   agent-facing instructions, so that learner docs stay free of my workflow rules.
10. As the maintainer, I want an explicit project-status and no-support statement, so that
    public interest does not turn into a maintenance obligation.
11. As the maintainer, I want the theory chapter and contract documentation to be part of
    each phase's definition of done, so that pedagogy is written while the concept is
    fresh instead of retrofitted.

## Implementation Decisions

**Positioning**

- Primary reader: an anonymous developer learning RAG hands-on. Pacing and scope
  authority: the maintainer. The repo remains a learning project that doubles as a
  playbook — in that order.
- "Playbook" means a public reference implementation. The repo is still explicitly not a
  commercial product, hosted service, or supported product; the contributor/agent
  instructions are updated in the rework so their self-description matches this
  positioning instead of the previous private-learning-project framing.
- The playbook promises "production-shaped reference implementation with dated decisions",
  explicitly not "state of the art". No claim in the docs may depend on staying current;
  time-sensitive claims carry the date they were last verified.
- Existing constraints are elevated to product features and stated as such: open-source
  only, CPU-only (4-core/8 GB design floor), no RAG frameworks, a real, properly-licensed
  corpus (English Wikipedia, CC BY-SA 4.0 with attribution), everything re-runnable from a
  clean checkout, no data artifacts in git. Feature status carries proof obligations:
  first-run downloads (embedding model, LLM weights, container images — several gigabytes)
  are documented up front, and the 8 GB floor is validated and recorded when the
  embedding-model and LLM decisions land.

**Architecture: stage = module**

- The pipeline is sliced into single-responsibility stages. Initial taxonomy — offline:
  **fetch** (source → raw files), **convert** (raw files → clean Markdown corpus),
  **chunk** (corpus → chunk records with metadata), **embed** (chunk records → vectors),
  **load** (chunk records + vectors → database; load owns the database schema, including
  index creation); online: **retrieve** (question → ranked chunks), **assemble**
  (question + ranked chunks → prompt), **generate** (prompt → grounded answer with
  citations). Roadmap phases may land more than one stage, but stage boundaries stay.
- This taxonomy is the starting point, not a ceiling: a backlog phase that introduces a
  genuinely new responsibility (sparse retrieval and fusion, query transformation,
  reranking, guardrails, …) may add or split a stage, amending this taxonomy in the same
  change. What stays forbidden is speculative slicing — no module exists before the phase
  that needs it.
- Offline stages are deep modules: one subpackage, a small public entry point, and a
  runnable command each. Inputs and outputs are artifacts — files on disk or database
  state — with a documented contract; intermediate artifacts are inspectable between
  stages.
- Online stages run in one process per question. Their contract is the documented entry
  point plus step-level logs of every intermediate (query, retrieved chunks with scores,
  assembled prompt, answer). **retrieve** embeds the question with the same model the
  offline **embed** stage uses — a deliberate coupling, named here and pinned as a dated
  decision when the model is chosen.
- **evaluate** is a cross-cutting harness, not a pipeline stage: its input is a checked-in
  gold-question set plus a pinned system configuration, its output a dated metrics report
  artifact. Results are recorded and compared, not asserted in tests — LLM-based metrics
  are not deterministic. It is exempt from the stage-module rules and lands with the
  backlog's evaluation phase.
- No orchestration framework, no plugin registry, no abstract interfaces until a second
  real implementation of a stage exists (the planned Docling ingestion path is the first
  legitimate trigger). SRP applies at the stage level; inside a stage, plain explicit
  functions.
- Corpus swap is a documented reader path with honest edges: the chunk-record contract
  uses corpus-neutral field names (source identifier, section path, citation label) with
  Wikipedia-article values as the first instantiation. Swapping a corpus means reimplementing
  fetch and convert for the new source **and** adapting the chunker's structural logic and
  citation fields to the new document structure; the contracts document exactly which
  fields downstream stages require, so this blast radius is explicit rather than
  discovered.

**Documentation taxonomy**

- README: learner front door — what this is, what is landed vs. planned (a status table
  gating every runnable-experience claim), quick start with its first-run cost, pipeline
  overview, and the project-status/no-support statement.
- This PRD: product big picture (audience, promises, pillars). Amended when a recorded
  decision changes it — taxonomy amendments update the architecture section in the same
  change — otherwise stable.
- Roadmap: living execution plan — phases, backlog, and the dated decision log. Single
  source of truth for decisions; other docs link to it. The rework changes its
  presentation only: each phase is annotated with the stage(s) it lands; phase count,
  ordering, and content are unchanged.
- Theory chapters: one concise chapter per building block in the docs area, written in
  the same phase as the code, cross-linked from module docstrings and the README pipeline
  overview. No duplication — a concept is explained exactly once.
- Concept map (`docs/concepts.md`): the ubiquitous language — every tracked RAG concept,
  defined once, with its place in the playbook. Its maintenance convention is stated in the
  map itself and recorded in the roadmap's 2026-07-11 concept-coverage decision.
- Contributor/agent instructions stay in their own files, never leak into reader docs,
  and are updated by the rework to the new positioning.
- Definition of done for every future phase grows to: code + tests + theory chapter +
  documented stage contract + updated README status.

**Corpus**

- English Wikipedia — the articles of the 20 current Premier League clubs — is the single
  corpus, fetched at runtime via the MediaWiki API. It is a feature: a real heading
  hierarchy (article → == section == → === subsection ===) keeps structure-aware chunking
  a genuine lesson instead of a toy, and the English content reads to any learner. That the
  domain is more conventional than a richly-structured reference corpus is acknowledged in the
  docs; the gain — runs on any laptop, reads to anyone — is the deliberate reason.
- Licensing: Wikipedia text is CC BY-SA 4.0 — properly licensed, not public domain. The
  claim is verified against Wikipedia's copyright terms when the fetch stage lands and
  recorded as a dated decision. Because `data/` is gitignored (the corpus is fetched at
  runtime, never redistributed in git) the repo incurs no share-alike obligation; the one
  live requirement is attribution on displayed excerpts, satisfied at the point of display.

**Scope of this PRD**

- The deliverable is the documentation rework: README rewrite (audience, status table,
  quick start with costs, pipeline overview, support policy), roadmap stage annotation,
  reader/contributor doc separation including the contributor/agent instruction update,
  and the new definition of done. It is planned and reviewed as its own change; pipeline
  phases then proceed per the roadmap under the new definition of done.

## Testing Decisions

- A good test exercises a stage's external contract — given this input, that output —
  never its internal helpers. Fixtures are small, checked-in samples (e.g. a truncated
  Wikipedia extract); real corpus data stays out of git.
- Every offline stage gets contract tests on its public entry point. Determinism is
  promised honestly, per stage: **convert** and **chunk** are pure transforms — same
  input artifact, same output, asserted exactly. **embed** promises reproducibility
  within a tolerance (similarity against checked-in reference vectors on small fixtures),
  never bitwise equality — CPU floating-point results vary across machines. **fetch**
  promises idempotence (re-running overwrites cleanly), not determinism — the source is a
  living corpus and edits to the articles (squads, managers, honours) legitimately change
  its output.
- Documentation verification is a manual review-checklist item — links and referenced
  paths must exist when a change lands; no link-checker tooling is built under this PRD.
- The quick start is verified from a clean checkout each time a phase lands, and the
  verification date is recorded with the README status. Between phases, external
  dependencies (the Wikipedia API, model hosting) can rot; the support policy states
  this explicitly.
- Prior art: the existing smoke test (import + version) establishes the pattern of testing
  through the package's public surface; stage contract tests extend it.

## Out of Scope

- Any pipeline phase implementation (fetch, chunking, …) — those follow the roadmap,
  which this PRD does not reorder or refill (the rework annotates phases, it does not
  reorganize them).
- A rendered documentation site (MkDocs or similar), link-checker tooling, or any other
  docs infrastructure — plain Markdown in the repo is the medium.
- Multi-corpus support or connector abstractions in code — the swap path is
  documentation until a second implementation is actually built.
- Guided exercises, quizzes, or course-style material — the playbook explains and
  demonstrates; it does not tutor.
- Any state-of-the-art guarantee, support SLA, or contribution program.
- Changes to the tech stack.

## Further Notes

Known risks, accepted deliberately:

- **Effort roughly doubles per phase.** Theory chapters and contract docs are real work.
  Mitigation: they are written in-phase while the concept is fresh; chapters stay concise.
- **Incompleteness is public.** Mitigated by the chapter-by-chapter framing and the README
  status table that gates every runnable-experience claim.
- **CC BY-SA is not public domain.** Wikipedia text is properly licensed, not public
  domain — a genuine step down from a public-domain corpus. Neutralized by the gitignored,
  runtime-fetched corpus (no distribution event, so no share-alike attaches to the repo)
  and by attribution on every displayed excerpt.
- **The corpus is volatile.** Squads, managers, and league membership change continuously,
  so fetch is idempotent by design, not deterministic — re-running legitimately changes its
  output. This makes the incremental-ingestion and drift-detection backlog items more
  naturally motivated, not less.
- **Aggregation questions are a real limit.** "Which club has won the most titles?" needs
  cross-article aggregation a single-pass top-k retriever answers poorly — an honest demo
  limitation that motivates the multi-hop/agentic backlog, not a defect to hide.
- **bge-small is weaker than a full multilingual model.** A deliberate trade for a model
  that fits the 4-core/8 GB floor and reads to an English audience; recorded as a trade,
  not a silent downgrade.
- **The demo domain is more conventional than a richly-structured reference corpus.** Football-over-
  Wikipedia is closer to a standard RAG demo; sectioned articles keep it above a toy blog
  post, but the domain-richness trade is real and named. The gain — runs on any laptop,
  reads to anyone — is the deliberate reason.
- **Pinned choices and external dependencies age.** Model choices rot, the Wikipedia API
  and model hosting can change between phases. Mitigated by dating every decision and
  every quick-start verification, and by the explicit support policy — never by promising
  currency.
- **The 8 GB floor is a constraint to validate, not a verified claim.** The embedding
  model (bge-small-en ≈ 130 MB), the ≈ 3 B granite4:micro (≈ 2.1 GB served), and Postgres
  must coexist at query time; feasibility is confirmed and recorded when the model
  decisions land (see the roadmap hardware-floor decision).
- **Over-slicing temptation.** The taxonomy may only grow when a phase lands a genuinely
  new responsibility, by amending this PRD in the same change; speculative granularity
  and speculative interfaces remain forbidden. Simple over clever remains the rule.
