"""RAG over German law texts — pipeline package.

Offline ingestion stages land as subpackages one roadmap phase at a time
(fetch → convert → chunk → embed → load); the online path (retrieve →
assemble → generate) follows, with evaluation as a cross-cutting harness.
See docs/roadmap.md.
"""

from pathlib import Path

__version__ = "0.1.0"

# Stage-handoff directories — each producer's output directory is the next stage's
# input; the stage CLIs use these as their --*-dir defaults, and the Makefile passes
# no overrides, so a handoff moves with one edit here.
RAW_DIR = Path("data/raw")
CORPUS_DIR = Path("data/corpus")
CHUNKS_DIR = Path("data/chunks")
EMBEDDINGS_DIR = Path("data/embeddings")
