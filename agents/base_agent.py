"""
RyzenSense v2 - Base Agent
All agents inherit from this class.
"""

import time
from datetime import datetime


class BaseAgent:
    """
    Base class for all RyzenSense agents.
    Provides lifecycle management, event handling, and logging.
    """

    def __init__(self):
        self.name = "base"
        self.orchestrator = None
        self._running = False
        self.last_action = "idle"
        self._log_buffer = []

    def run(self):
        """Override in subclass for background loop agents."""
        pass

    def stop(self):
        """Signal the agent to stop its loop."""
        self._running = False

    def on_event(self, event: str, data: dict, sender: str):
        """Override to react to events from other agents."""
        pass

    def broadcast(self, event: str, data: dict = None):
        """Send an event to all other agents via orchestrator."""
        if self.orchestrator:
            self.orchestrator.broadcast(self.name, event, data)

    def get_agent(self, name: str):
        """Get another agent by name from the orchestrator."""
        if self.orchestrator:
            return self.orchestrator.get_agent(name)
        return None

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"  [{self.name} {ts}] {msg}"
        self._log_buffer.append(entry)
        print(entry)

    def set_action(self, action: str):
        self.last_action = action