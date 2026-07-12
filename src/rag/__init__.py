"""RAG over German law texts — pipeline package.

Offline ingestion stages land as subpackages one roadmap phase at a time
(fetch → convert → chunk → embed → load); the online path (retrieve →
assemble → generate) follows, with evaluation as a cross-cutting harness.
See docs/roadmap.md.
"""

__version__ = "0.1.0"
