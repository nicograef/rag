"""Run the learner web app: ``python -m rag.api`` (what ``make serve`` calls).

Serves the module-level ``app`` with a **single** uvicorn worker on ``API_HOST``/``API_PORT``
(defaults ``127.0.0.1:8000``). One worker by design: the warm embedding model and its torch
runtime live in-process, and the app shares the 4-core/8 GB floor with Postgres and Ollama —
multiple workers would each load their own copy of the model. No ``--reload`` (a reload
subprocess would reload the model).
"""

import os

import uvicorn

from rag.api import app


def main() -> None:
    """Start the single-worker uvicorn server on the configured host/port."""
    uvicorn.run(
        app,
        host=os.environ.get("API_HOST", "127.0.0.1"),
        port=int(os.environ.get("API_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
