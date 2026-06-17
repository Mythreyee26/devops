"""
The agent's tools.

READ-ONLY tools (`get`, `describe`, `logs`) let the agent observe the cluster.
WRITE tools (`scale`, `set image`, `rollout restart`, `delete pod`) let it FIX
problems — but every write is gated behind two safety steps:
  1. a server-side `--dry-run` that validates the change without applying it, and
  2. an explicit human y/N approval prompt before the real command runs.
So the agent can never change the cluster on its own; you always confirm first.
(Set K8S_AGENT_AUTO_APPROVE=1 to skip the prompt — only for trusted demos.)

The @tool decorator (from LangChain) turns a plain Python function into something
the LLM can call. The function's docstring is what the model reads to decide
when to use it — so the docstrings are written for the model, not just for us.
"""

import os
import subprocess
from langchain_core.tools import tool

def _ns() -> str:
    """The namespace to operate in. Defaults to 'default'; override with the
    K8S_NAMESPACE env var (the CLI's --namespace flag sets this for you)."""
    return os.getenv("K8S_NAMESPACE", "default")


def _run(cmd: list[str], timeout: int = 30) -> str:
    """Run a kubectl command and return its output as text."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return "ERROR: kubectl not found on PATH."
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s."

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
    return _run(["kubectl", "get", "pods", "-n", _ns()])


@tool
def describe_pod(pod_name: str) -> str:
    """Show detailed information about ONE pod, including the Events section that
    explains WHY it is unhealthy. This is the most important debugging tool.
    You must pass the exact pod_name from list_pods first."""
    return _run(["kubectl", "describe", "pod", pod_name, "-n", _ns()])


@tool
def get_pod_logs(pod_name: str) -> str:
    """Get the recent logs (last 50 lines) printed by a pod's container. Useful
    when a container starts then crashes (CrashLoopBackOff). Pass the exact
    pod_name from list_pods."""
    return _run(["kubectl", "logs", pod_name, "-n", _ns(), "--tail=50"])


@tool
def get_events() -> str:
    """List recent cluster events in the default namespace, oldest to newest.
    Good for spotting warnings like failed image pulls or scheduling problems."""
    return _run(["kubectl", "get", "events", "-n", _ns(), "--sort-by=.lastTimestamp"])


@tool
def list_deployments() -> str:
    """List deployments in the default namespace showing desired vs available
    replicas (e.g. 0/1 means it wanted 1 pod but 0 are ready)."""
    return _run(["kubectl", "get", "deployments", "-n", _ns()])


@tool
def list_deployment_images() -> str:
    """List every deployment and the container image it is configured to use. Use this
    to DISCOVER the correct image before fixing a broken deployment — e.g. find a
    healthy sibling deployment running the same app and reuse its image with
    set_deployment_image, instead of guessing. Format: 'deployment  container=image'."""
    jsonpath = (
        '{range .items[*]}{.metadata.name}{"  "}'
        '{range .spec.template.spec.containers[*]}{.name}={.image}{" "}{end}'
        '{"\\n"}{end}'
    )
    return _run(["kubectl", "get", "deployments", "-n", _ns(), "-o", f"jsonpath={jsonpath}"])


@tool
def wait_for_deployment(deployment_name: str) -> str:
    """Wait up to ~25s for a deployment to finish rolling out and become healthy
    (kubectl rollout status). Call this ONCE after applying a fix to confirm it
    actually worked — it blocks until the new pods are Ready or it times out, so you
    do NOT need to poll list_pods repeatedly."""
    return _run(
        ["kubectl", "rollout", "status", f"deployment/{deployment_name}",
         "-n", _ns(), "--timeout=25s"],
        timeout=30,
    )


# --------------------------------------------------------------------------
# WRITE tools — these CHANGE the cluster, so they go through _apply(), which
# dry-runs the command, then asks the human to approve before really running it.
# --------------------------------------------------------------------------

def _confirm(action: str, cmd: list[str]) -> bool:
    """Ask the human to approve a cluster-changing command. Returns True if yes."""
    if os.getenv("K8S_AGENT_AUTO_APPROVE") == "1":
        print(f"\n  [auto-approved] {action}", flush=True)
        return True
    print(f"\n  PROPOSED FIX: {action}", flush=True)
    print(f"  Command:      {' '.join(cmd)}", flush=True)
    try:
        answer = input("  Apply this change? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in ("y", "yes")


def _apply(action: str, cmd: list[str], dry_run_cmd: list[str] | None = None) -> str:
    """Validate (dry-run) -> ask the human -> run the real command."""
    # 1. Server-side dry-run: catches bad names/images BEFORE we ask to apply.
    if dry_run_cmd is not None:
        preview = _run(dry_run_cmd)
        if preview.startswith(("Command failed", "ERROR")):
            return f"Aborted — dry-run validation failed:\n{preview}"
    # 2. Human approval.
    if not _confirm(action, cmd):
        return "Change cancelled by the user. No action was taken."
    # 3. Apply for real.
    return f"Applied: {action}\n{_run(cmd)}"


@tool
def scale_deployment(deployment_name: str, replicas: int) -> str:
    """Scale a deployment to a given number of replicas (kubectl scale). Use this to
    bring a deployment that is scaled to 0 back up, or to add/remove capacity.
    This CHANGES the cluster and requires human approval."""
    cmd = ["kubectl", "scale", f"deployment/{deployment_name}",
           f"--replicas={replicas}", "-n", _ns()]
    return _apply(
        f"Scale deployment '{deployment_name}' to {replicas} replica(s)",
        cmd, dry_run_cmd=cmd + ["--dry-run=server"],
    )


@tool
def set_deployment_image(deployment_name: str, container_name: str, image: str) -> str:
    """Update a deployment's container image (kubectl set image). This is the FIX for
    ImagePullBackOff / ErrImagePull caused by a wrong or non-existent image tag — set
    it to a valid image. This CHANGES the cluster and requires human approval."""
    cmd = ["kubectl", "set", "image", f"deployment/{deployment_name}",
           f"{container_name}={image}", "-n", _ns()]
    return _apply(
        f"Set image of container '{container_name}' in '{deployment_name}' to '{image}'",
        cmd, dry_run_cmd=cmd + ["--dry-run=server"],
    )


@tool
def restart_deployment(deployment_name: str) -> str:
    """Restart a deployment by rolling its pods (kubectl rollout restart). Use this to
    recover pods stuck in a transient bad state (e.g. after a config/secret change).
    This CHANGES the cluster and requires human approval."""
    cmd = ["kubectl", "rollout", "restart", f"deployment/{deployment_name}", "-n", _ns()]
    return _apply(f"Restart deployment '{deployment_name}' (roll its pods)", cmd)


@tool
def delete_pod(pod_name: str) -> str:
    """Delete a single pod (kubectl delete pod). Its Deployment/ReplicaSet will create a
    fresh replacement, so this is a safe way to clear a stuck pod. Pass the exact
    pod_name from list_pods. This CHANGES the cluster and requires human approval."""
    cmd = ["kubectl", "delete", "pod", pod_name, "-n", _ns()]
    return _apply(f"Delete pod '{pod_name}' (its controller will recreate it)", cmd)


# The list we hand to the agent: read-only observers + approval-gated fixers.
TOOLS = [
    list_pods, describe_pod, get_pod_logs, get_events, list_deployments,
    list_deployment_images, wait_for_deployment,
    scale_deployment, set_deployment_image, restart_deployment, delete_pod,
]
