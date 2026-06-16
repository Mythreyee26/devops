"""
The agent's tools — each one runs a READ-ONLY kubectl command and returns text.

Safety guardrail: every command here is `get`, `describe`, or `logs`. There is
NO delete/apply/edit. The agent can observe the cluster but never change it.

The @tool decorator (from LangChain) turns a plain Python function into something
the LLM can call. The function's docstring is what the model reads to decide
when to use it — so the docstrings are written for the model, not just for us.
"""

import subprocess
from langchain_core.tools import tool

NAMESPACE = "default"


def _run(cmd: list[str]) -> str:
    """Run a kubectl command and return its output as text."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "ERROR: kubectl not found on PATH."
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 30s."

    if result.returncode != 0:
        return f"Command failed: {result.stderr.strip() or result.stdout.strip()}"

    out = result.stdout.strip() or "(no output)"
    # A `describe` can be huge. Keep the TAIL, because kubectl puts the most
    # important part — the Events section — at the bottom.
    if len(out) > 6000:
        out = "...(truncated)...\n" + out[-6000:]
    return out


@tool
def list_pods() -> str:
    """List all pods in the default namespace with their STATUS (Running,
    ImagePullBackOff, CrashLoopBackOff, etc.). Start here to find which pod is
    unhealthy and to get its exact name."""
    return _run(["kubectl", "get", "pods", "-n", NAMESPACE])


@tool
def describe_pod(pod_name: str) -> str:
    """Show detailed information about ONE pod, including the Events section that
    explains WHY it is unhealthy. This is the most important debugging tool.
    You must pass the exact pod_name from list_pods first."""
    return _run(["kubectl", "describe", "pod", pod_name, "-n", NAMESPACE])


@tool
def get_pod_logs(pod_name: str) -> str:
    """Get the recent logs (last 50 lines) printed by a pod's container. Useful
    when a container starts then crashes (CrashLoopBackOff). Pass the exact
    pod_name from list_pods."""
    return _run(["kubectl", "logs", pod_name, "-n", NAMESPACE, "--tail=50"])


@tool
def get_events() -> str:
    """List recent cluster events in the default namespace, oldest to newest.
    Good for spotting warnings like failed image pulls or scheduling problems."""
    return _run(["kubectl", "get", "events", "-n", NAMESPACE, "--sort-by=.lastTimestamp"])


@tool
def list_deployments() -> str:
    """List deployments in the default namespace showing desired vs available
    replicas (e.g. 0/1 means it wanted 1 pod but 0 are ready)."""
    return _run(["kubectl", "get", "deployments", "-n", NAMESPACE])


# The list we hand to the agent.
TOOLS = [list_pods, describe_pod, get_pod_logs, get_events, list_deployments]
