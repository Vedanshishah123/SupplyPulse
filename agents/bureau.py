"""Builds the Bureau that runs every agent in one process (see agents.run_all and tests.chat_client)."""
import os

from uagents import Bureau

from agents import events, financial, hazard, mitigation, monitor, orchestrator, scorer


def build(extra=()):
    port = int(os.getenv("BUREAU_PORT", "8010"))
    bureau = Bureau(port=port, endpoint=[f"http://127.0.0.1:{port}/submit"])
    agents = (orchestrator.agent, hazard.agent, events.agent, financial.agent, scorer.agent, mitigation.agent,
              monitor.agent)
    for a in (*agents, *extra):
        bureau.add(a)
    return bureau

