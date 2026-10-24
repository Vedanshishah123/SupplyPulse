"""Everything in one process: the mailbox-connected orchestrator plus all internal agents.

    python -m agents.run_all

Internal agents talk to each other in memory (no network hop), and only the chat travels through the
Agentverse mailbox. The mailbox must already be connected (one-time, via the Inspector). Do not run this
at the same time as `agents.orchestrator`: two processes would share one mailbox.
"""
from agents.bureau import build

if __name__ == "__main__":
    build().run()
