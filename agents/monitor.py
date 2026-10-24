"""Monitor agent: periodically tells the orchestrator to re-scan every Pro user's suppliers.

The orchestrator owns the supplier data and the change detection (alert only when a
supplier's risk band changes, to avoid alert fatigue). Interval: MONITOR_INTERVAL_S
(default 86400 = daily; use 60 for demos).
"""
import os
import time

from uagents import Agent, Context

from agents.common import PORTS, address, seed
from models.messages import MonitorTick

PORTS.setdefault("monitor", 8007)
INTERVAL_S = float(os.getenv("MONITOR_INTERVAL_S", "86400"))
agent = Agent(name="monitor", seed=seed("monitor"), port=PORTS["monitor"],
              endpoint=[f"http://127.0.0.1:{PORTS['monitor']}/submit"])
_started = False


@agent.on_interval(period=INTERVAL_S)
async def tick(ctx: Context):
    global _started
    if not _started:  # skip the immediate first run so the orchestrator is up before the first tick
        _started = True
        return
    await ctx.send(address("orchestrator"), MonitorTick(ts=time.time()))
