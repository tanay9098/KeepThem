"""Central configuration: connection settings, the demo 'today', and risk thresholds."""
import os
from datetime import date, timedelta

# --- FalkorDB connection -----------------------------------------------------
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD") or None
GRAPH_NAME = os.getenv("KEEPTHEM_GRAPH", "keepthem")
DB_MODE = os.getenv("KEEPTHEM_DB", "auto")  # server | embedded | auto
EMBEDDED_PATH = os.getenv("KEEPTHEM_EMBEDDED_PATH", ".keepthem/falkordb.rdb")

# --- Agent -------------------------------------------------------------------
MODEL = os.getenv("KEEPTHEM_MODEL", "claude-opus-5-5")

# --- The synthetic company's "today" -------------------------------------------
# Fixed (not date.today()) so every run of the demo gives the same answers.
AS_OF = os.getenv("KEEPTHEM_AS_OF", "2026-10-01")


def days_before(n: int, as_of: str = AS_OF) -> str:
    """ISO date string n days before `as_of` (ISO strings compare correctly in Cypher)."""
    return (date.fromisoformat(as_of) - timedelta(days=n)).isoformat()


# --- Risk model thresholds -----------------------------------------------------
OVERLOAD_THRESHOLD = 8        # direct reports at which a manager is "overloaded"
PEER_WINDOW_DAYS = 240        # how far back a resignation still counts as a signal
DECISION_WINDOW_DAYS = 365    # adverse decisions older than this are ignored
EVENT_WINDOW_DAYS = 240       # negative events older than this are ignored
STAGNATION_DAYS = 730         # no promotion for this long (and tenure >= 24m) => stagnation
HIGH_RISK = 65                # score >= HIGH_RISK   -> "high"
MEDIUM_RISK = 25              # score >= MEDIUM_RISK -> "medium", else "low"

NEGATIVE_DECISIONS = ["promotion_denied", "raise_denied", "transfer_denied", "project_removal"]
NEGATIVE_EVENTS = ["complaint", "manager_change", "reorg", "project_cancelled", "overtime_spike"]
