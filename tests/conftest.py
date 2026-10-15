"""Tests run against a REAL FalkorDB (server if reachable, else embedded falkordblite) using a
throw-away graph, so they exercise the actual Cypher - nothing is mocked at the DB layer."""
import pytest

from keepthem import config, db, seed


@pytest.fixture(scope="session")
def _backend(tmp_path_factory):
    config.GRAPH_NAME = "keepthem_test"
    config.EMBEDDED_PATH = str(tmp_path_factory.mktemp("fdb") / "test.rdb")
    try:
        kg = db.connect()
    except ConnectionError as ex:
        pytest.skip(f"no FalkorDB available: {ex}")
    db.set_kg(kg)
    yield kg
    kg.delete()
    db.set_kg(None)


@pytest.fixture(scope="session")
def kg(_backend):
    """Seeded graph shared by read-only tests."""
    seed.seed(_backend, verbose=False)
    return _backend


@pytest.fixture()
def fresh_kg(_backend):
    """Re-seeded graph for tests that write."""
    seed.seed(_backend, verbose=False)
    return _backend
