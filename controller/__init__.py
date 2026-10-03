"""PERCH Controller - Automated canary release decision engine.

This package contains the core controller logic for observing metrics,
making rollback decisions, and managing traffic weights.
"""

from .config import ControllerConfig, DecisionConfig, DecisionMode, RampConfig, from_env
from .decision import Decision, MetricSample, evaluate
from .main import run
from .metrics import PrometheusClient
from .proxy_writer import ProxyWriter
from .ramp import RampState, apply_decision, initial_state

__all__ = [
    "ControllerConfig",
    "DecisionConfig",
    "DecisionMode",
    "RampConfig",
    "from_env",
    "Decision",
    "MetricSample",
    "evaluate",
    "run",
    "PrometheusClient",
    "ProxyWriter",
    "RampState",
    "apply_decision",
    "initial_state",
]
