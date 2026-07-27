"""
Blackfong Agent Implementations

Standardized Agent wrappers for:
- JARVIS: General reasoning assistant
- Lilo: Certification training
- Stitch: Python learning
- Coding: Python-first development assistant

All agents implement the Agent interface and can:
- Process user queries
- Invoke tools via ToolDispatcher
- Emit reasoning traces as events
- Participate in multi-agent coordination
"""

from .jarvis_agent import JARVISAgent
from .lilo_agent import LiloAgent
from .stitch_agent import StitchAgent
from .coding_agent import CodingAgent

__all__ = [
    "JARVISAgent",
    "LiloAgent",
    "StitchAgent",
    "CodingAgent",
]
