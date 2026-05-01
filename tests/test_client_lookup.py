"""Unit tests for agent.client_lookup — no LLM involved."""
import pytest

from agent.client_lookup import load_clients, lookup_client
from agent.models import ClientRecord

# Minimal fixture roster — avoids filesystem dependency in most tests.
_CLIENTS = [
    ClientRecord(
        id="c1", name="Saubermann GmbH", short_names=["Saubermann"],
        address_line1="Mariahilfer Straße 42", address_line2="1060 Wien",
        uid="ATU12345678",
    ),
    ClientRecord(
        id="c2", name="Familie Gruber", short_names=["Gruber"],
        address_line1="Dorfstraße 3", address_line2="4814 Neukirchen",
        uid=None,
    ),
    ClientRecord(
        id="c3", name="Alpin Holzbau GmbH", short_names=["Alpin Holzbau", "Alpin"],
        address_line1="Gewerbepark 15", address_line2="6330 Kufstein",
        uid="ATU67890123",
    ),
    ClientRecord(
        id="c4", name="Stadler Consulting OG", short_names=["Stadler"],
        address_line1="Landstraßer Hauptstraße 60", address_line2="1030 Wien",
        uid="ATU89012345",
    ),
]


def test_exact_name_match():
    result = lookup_client("Saubermann GmbH", _CLIENTS)
    assert result is not None
    assert result.id == "c1"


def test_short_name_match():
    result = lookup_client("Saubermann", _CLIENTS)
    assert result is not None
    assert result.id == "c1"


def test_partial_name_resolves_to_full_record():
    result = lookup_client("Gruber", _CLIENTS)
    assert result is not None
    assert result.id == "c2"
    assert result.name == "Familie Gruber"


def test_single_word_short_name():
    result = lookup_client("Stadler", _CLIENTS)
    assert result is not None
    assert result.id == "c4"


def test_multi_word_short_name():
    result = lookup_client("Alpin Holzbau", _CLIENTS)
    assert result is not None
    assert result.id == "c3"


def test_minor_typo_still_matches():
    # "Sauberman" missing one 'n'
    result = lookup_client("Sauberman GmbH", _CLIENTS)
    assert result is not None
    assert result.id == "c1"


def test_completely_unrelated_ref_returns_none():
    result = lookup_client("Unbekannte Firma XYZ", _CLIENTS)
    assert result is None


def test_empty_ref_returns_none():
    result = lookup_client("", _CLIENTS)
    assert result is None


def test_whitespace_only_ref_returns_none():
    result = lookup_client("   ", _CLIENTS)
    assert result is None


def test_empty_client_list_returns_none():
    result = lookup_client("Saubermann", [])
    assert result is None


def test_load_clients_reads_real_file():
    from pathlib import Path
    path = Path(__file__).parent.parent / "data" / "clients.json"
    clients = load_clients(path)
    assert len(clients) >= 10
    assert all(isinstance(c, ClientRecord) for c in clients)


def test_load_clients_and_lookup_end_to_end():
    from pathlib import Path
    path = Path(__file__).parent.parent / "data" / "clients.json"
    clients = load_clients(path)
    result = lookup_client("Kern", clients)
    assert result is not None
    assert "Kern" in result.name
