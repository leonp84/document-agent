"""Client lookup: fuzzy match a raw client ref against the clients roster."""
import json
from pathlib import Path

from rapidfuzz import fuzz, process

from agent.models import ClientRecord

_DEFAULT_CLIENTS_PATH = Path(__file__).parent.parent / "data" / "clients.json"
_DEFAULT_THRESHOLD = 75


def load_clients(path: Path = _DEFAULT_CLIENTS_PATH) -> list[ClientRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ClientRecord(**c) for c in data]


def lookup_client(
    ref: str,
    clients: list[ClientRecord],
    threshold: int = _DEFAULT_THRESHOLD,
) -> ClientRecord | None:
    """Return the best-matching ClientRecord, or None if nothing clears the threshold."""
    if not ref.strip() or not clients:
        return None

    # Each client contributes its canonical name + all short_names as candidates.
    # We keep a parallel index so we can map winner back to the ClientRecord.
    candidates: list[str] = []
    index: list[ClientRecord] = []
    for client in clients:
        for label in [client.name] + client.short_names:
            candidates.append(label)
            index.append(client)

    result = process.extractOne(ref, candidates, scorer=fuzz.token_sort_ratio)
    if result is None or result[1] < threshold:
        return None

    return index[result[2]]
