"""Service-local cache and admission limits."""

CACHE_TTL_SECONDS = 60
CACHE_CAPACITY = 128
ACTIVE_REQUESTS = 8
WAITING_REQUESTS = 16


def load_schema(path):
    """Load and validate the caller's JSON entity schema."""
    from pathlib import Path

    from contracts.models import EntitySchema

    return EntitySchema.model_validate_json(Path(path).read_text(encoding="utf-8"))
