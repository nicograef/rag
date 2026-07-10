# PRD: RAG Playbook

The product-level big picture for this repository. The execution plan (phases, backlog,
dated decisions) lives in [../roadmap.md](../roadmap.md); this document states what the
repo is, for whom, and what "good" looks like.

## Problem Statement

People who want to learn how RAG actually works have two bad options. Tutorials are
toy-grade: fixed-size chunking on a blog post, a cloud embedding API, no evaluation, no
production concerns. Framework codebases (LangChain, LlamaIndex, Haystack) are
production-grade but hide every moving part behind abstractions — reading them teaches the
framework, not RAG. There is no self-contained, framework-free reference a developer can
clone and run end to end on an ordinary dev machine, with the theory explained next to the
code that implements it.

This repository is already positioned to fill that gap — open-source only, CPU-only, no
frameworks, real public-domain corpus — but its documentation is written for one person
(the maintainer, as a private learning project). A stranger landing on the repo cannot tell
what it is, how to run it, how the pipeline is sliced, or which claims are current. The big
picture exists only implicitly in the roadmap; there is no product-level statement of
audience, promises, and non-promises.

## Solution

Reposition the repository as a **RAG playbook**: a production-shaped, self-hosted,
framework-free reference implementation of a complete RAG system over a real corpus
(German federal law), that a learner can clone and run on their own machine.

The playbook rests on four pillars:

1. **Small modules with artifact contracts.** The pipeline is sliced into
   single-responsibility stages. Each stage has one documented input artifact and one
   documented output artifact (files on disk or database state), is runnable on its own,
   and its output is inspectable before the next stage consumes it. The data flow is the
   table of contents.
2. **Theory next to code.** Every building block (chunking, embeddings, vector indexes,
   hybrid search, reranking, evaluation, …) gets a concise theory chapter, written in the
   same increment as the code, cross-linked in both directions. The repo is self-contained:
   a learner needs no external course to follow it.
3. **Honest currency instead of "state of the art".** RAG fashion rots in months. The
   playbook teaches durable building blocks and records its concrete choices (models,
   parameters, trade-offs) as dated decisions a reader can re-evaluate — it never claims
   to be the current best.
4. **Grows chapter by chapter.** The playbook is explicitly incomplete and says so. Each
   landed phase is complete on its own — code, tests, theory chapter, runnable stage.
   Pacing stays with the maintainer; this is still a learning project first.

The learner experience: clone, run a few documented commands, watch a real corpus flow
stage by stage into a database, ask a question in the terminal, get a grounded answer with
citations — and at every stage, read why it works the way it does, then swap in their own
corpus by following the documented stage contracts.

## User Stories

1. As a learner, I want to clone the repo and bring the whole system up on my own dev
   machine with a few documented commands, so that I can run a real RAG system without
   cloud accounts, paid APIs, or a GPU.
2. As a learner, I want each pipeline stage to be a small single-responsibility module
   with a documented input and output artifact, so that I can run, inspect, and understand
   one stage at a time instead of debugging a monolith.
3. As a learner, I want a concise theory chapter per building block, cross-linked with the
   code that implements it, so that I understand why a technique exists before I read how
   it is implemented.
4. As a learner, I want the stage contracts documented well enough to swap in my own
   corpus, so that the playbook transfers to my domain instead of staying a demo.
5. As a learner, I want every concrete choice (embedding model, chunk parameters, index
   type, LLM) recorded as a dated decision with reasoning, so that I can judge what is
   still current and adapt it.
6. As a learner, I want a front-door README that states what the playbook covers, what is
   landed versus planned, and how to start, so that I can orient myself in minutes.
7. As the maintainer, I want the playbook framed as growing chapter by chapter, so that
   the public repo stays honest while incomplete and each landed phase stands alone.
8. As the maintainer, I want reader-facing documentation separated from contributor- and
   agent-facing instructions, so that learner docs stay free of my workflow rules.
9. As the maintainer, I want an explicit project-status and no-support statement, so that
   public interest does not turn into a maintenance obligation.
10. As the maintainer, I want the theory chapter and contract documentation to be part of
    each phase's definition of done, so that pedagogy is written while the concept is
    fresh instead of retrofitted.

## Implementation Decisions

**Positioning**

- Primary reader: an anonymous developer learning RAG hands-on. Pacing and scope
  authority: the maintainer. The repo remains a learning project that doubles as a
  playbook — in that order.
- The playbook promises "production-shaped reference implementation with dated decisions",
  explicitly not "state of the art". No claim in the docs may depend on staying current.
- Existing constraints are elevated to product features and stated as such: open-source
  only, CPU-only (8-core/16 GB design floor), no RAG frameworks, real public-domain
  corpus, everything re-runnable from a clean checkout, no data artifacts in git.

**Architecture: stage = module**

- The pipeline is sliced into single-responsibility stages. Canonical taxonomy —
  offline: **fetch** (source → raw files), **convert** (raw files → clean Markdown
  corpus), **chunk** (corpus → chunk records with metadata), **embed** (chunk records →
  vectors), **load** (chunks + vectors → database); online: **retrieve** (question →
  ranked chunks), **assemble** (question + chunks → prompt), **generate** (prompt →
  grounded answer with citations); cross-cutting: **evaluate** (gold questions →
  metrics). Roadmap phases may land more than one stage, but stage boundaries stay.
- Each stage is a deep module: one subpackage, a small public entry point, and a runnable
  command (CLI or make target). Inputs and outputs are artifacts — files on disk or
  database state — with a documented, stable contract; intermediate artifacts are
  inspectable between stages.
- No orchestration framework, no plugin registry, no abstract interfaces until a second
  real implementation of a stage exists (the planned Docling ingestion path is the first
  legitimate trigger). SRP applies at the stage level; inside a stage, plain explicit
  functions.
- "Swap your own corpus" is a documented reader path (implement the fetch/convert
  contracts for your source), not an abstraction layer.

**Documentation taxonomy**

- README: learner front door — what this is, what is landed vs. planned, quick start,
  pipeline overview, project-status/no-support statement.
- This PRD: product big picture (audience, promises, pillars). Stable; changes rarely.
- Roadmap: living execution plan — phases, backlog, and the dated decision log. Single
  source of truth for decisions; other docs link to it.
- Theory chapters: one concise chapter per building block in the docs area, written in
  the same phase as the code, cross-linked from module docstrings and the README pipeline
  overview. No duplication — a concept is explained exactly once.
- Contributor/agent instructions stay in their own files and never leak into reader docs.
- Definition of done for every future phase grows to: code + tests + theory chapter +
  documented stage contract + updated README status.

**Corpus**

- German federal law (gesetze-im-internet.de XML, public domain per § 5 UrhG) stays the
  single corpus. It is a feature: real structure (law → § → Absatz) makes structure-aware
  chunking a genuine lesson instead of a toy. The German-language limitation is
  acknowledged in the docs and offset by the swap-your-own-corpus path.

**First implementation slice**

- The documentation rework that executes this repositioning (README rewrite, roadmap
  restructure into the taxonomy above, reader/contributor doc separation, status
  statement) is the first follow-up, planned and reviewed as its own change. Pipeline
  phases then proceed per the roadmap under the new definition of done.

## Testing Decisions

- A good test exercises a stage's external contract — given this input artifact, that
  output artifact — never its internal helpers. Fixtures are small, checked-in samples
  (e.g. a truncated law XML); real corpus data stays out of git.
- Every stage gets contract tests on its public entry point. Determinism is part of the
  contract for offline stages: same input, same output.
- Documentation is verified mechanically where possible: links and referenced paths must
  exist (checked at review time), and the quick start must work from a clean checkout —
  re-runnability is itself the playbook's core testable promise.
- Prior art: the existing smoke test (import + version) establishes the pattern of testing
  through the package's public surface; stage contract tests extend it.

## Out of Scope

- Executing the documentation rework itself — this PRD specifies it; a follow-up plan
  lands it.
- Any pipeline phase implementation (fetch, chunking, …) — those follow the roadmap.
- A rendered documentation site (MkDocs or similar) — plain Markdown in the repo is the
  medium for now.
- Multi-corpus support or connector abstractions — the swap path is documentation, not
  code, until a second implementation is actually built.
- Guided exercises, quizzes, or course-style material — the playbook explains and
  demonstrates; it does not tutor.
- Any state-of-the-art guarantee, support SLA, or contribution program.
- Changes to the tech stack or to the roadmap's phase ordering.

## Further Notes

Known risks, accepted deliberately:

- **Effort roughly doubles per phase.** Theory chapters and contract docs are real work.
  Mitigation: they are written in-phase while the concept is fresh; chapters stay concise.
- **Incompleteness is public.** Mitigated by the chapter-by-chapter framing and a README
  that states landed vs. planned truthfully.
- **The German corpus limits relatability** for non-German readers. Accepted for its
  realism and licensing clarity; mitigated by the documented corpus-swap path.
- **Pinned versions and model choices will age.** Mitigated by dating every decision and
  never promising currency.
- **Over-slicing temptation.** The stage taxonomy is the floor and the ceiling: no finer
  module granularity, no speculative interfaces. Simple over clever remains the rule.
