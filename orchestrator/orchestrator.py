"""
RyzenSense v2 - Multi-Agent Orchestrator
Coordinates all agents: Telemetry, Tuning, Benchmark, Profile, Watchdog, Prediction
"""

import time
import threading
import json
from datetime import datetime
from typing import Optional


class AgentOrchestrator:
    """
    Central brain that manages all agents and their communication.
    Agents talk to each other via a shared message bus.
    """

    def __init__(self):
        self.agents = {}
        self._message_bus = []
        self._bus_lock = threading.Lock()
        self._running = False
        self._threads = []
        self.event_log = []

    def register(self, name: str, agent):
        """Register an agent with the orchestrator."""
        agent.orchestrator = self
        agent.name = name
        self.agents[name] = agent
        self._log(f"Agent registered: {name}")

    def broadcast(self, sender: str, event: str, data: dict = None):
        """Any agent can broadcast an event to all other agents."""
        message = {
            "timestamp": datetime.now().isoformat(),
            "sender": sender,
            "event": event,
            "data": data or {},
        }
        with self._bus_lock:
            self._message_bus.append(message)
        self.event_log.append(message)

        # Notify all other agents
        for name, agent in self.agents.items():
            if name != sender and hasattr(agent, "on_event"):
                try:
                    agent.on_event(event, data or {}, sender)
                except Exception as e:
                    self._log(f"Event delivery error to {name}: {e}")

    def run_all(self):
        """Start all agents in background threads."""
        self._running = True
        for name, agent in self.agents.items():
            if hasattr(agent, "run"):
                t = threading.Thread(target=agent.run, daemon=True, name=f"agent-{name}")
                t.start()
                self._threads.append(t)
                self._log(f"Started agent thread: {name}")

    def stop_all(self):
        """Signal all agents to stop."""
        self._running = False
        for agent in self.agents.values():
            if hasattr(agent, "stop"):
                agent.stop()

    def get_agent(self, name: str):
        return self.agents.get(name)

    def get_event_log(self, limit: int = 50) -> list:
        return self.event_log[-limit:]

    def status(self) -> dict:
        return {
            name: {
                "running": getattr(agent, "_running", False),
                "last_action": getattr(agent, "last_action", "idle"),
            }
            for name, agent in self.agents.items()
        }

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  [Orchestrator {ts}] {msg}")