"""Agent observability for letta-local.

Observer, not participant. The agent loop calls the recorder at phase
boundaries; the recorder decides what OTel data to emit. No coupling.
No new framework dependencies. Users point their OTLP exporter at
whatever backend they want.
"""

from letta.observability.agent_step_recorder import AgentStepRecorder

__all__ = ["AgentStepRecorder"]
