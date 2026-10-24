"""Seeds, ports and deterministic addresses for the agents in the Bureau."""
import os

from dotenv import load_dotenv
from uagents.crypto import Identity

load_dotenv()  # real environment variables win over .env

SEED_PREFIX = os.getenv("AGENT_SEED_PREFIX", "supplypulse-dev")
PORTS = {"orchestrator": 8000, "hazard": 8002, "scorer": 8003}


def seed(name: str) -> str:
    return f"{SEED_PREFIX}-{name}"


def address(name: str) -> str:
    return Identity.from_seed(seed(name), 0).address


def use_mailbox() -> bool:
    """Mailbox is how Agentverse/ASI:One reach the orchestrator; off for local tests."""
    return os.getenv("USE_MAILBOX", "1") == "1"
