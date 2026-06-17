"""
Kubernetes Troubleshooting Agent (LangGraph + Google Gemini).

Ask it in plain English what's wrong with your cluster. It uses read-only
kubectl tools to inspect pods, then explains the root cause and a fix.

Needs GOOGLE_API_KEY set in the environment.

Run interactively:
    python k8s_agent.py

Ask one question and exit:
    python k8s_agent.py "what is wrong with broken-app?"
"""

import argparse
import os
import sys

from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langchain_core.messages import HumanMessage

from llm import get_llm
from tools import TOOLS

SYSTEM_PROMPT = """\
You are a Kubernetes troubleshooting assistant for a local minikube cluster.
All resources are in the "{namespace}" namespace. You can both DIAGNOSE and FIX issues.

Read-only tools (inspect the cluster):
- list_pods               : list pods and their STATUS
- describe_pod(name)      : detailed info + Events explaining WHY a pod is unhealthy
- get_pod_logs(name)      : recent container logs (for crashes)
- get_events              : recent cluster events
- list_deployments        : deployments and replica counts
- list_deployment_images  : every deployment and the image it uses (find correct images)
- wait_for_deployment(name): block until a deployment finishes rolling out / becomes Ready

Fix tools (CHANGE the cluster — each one asks the human to approve before applying):
- scale_deployment(name, replicas)               : bring a scaled-to-0 deployment up
- set_deployment_image(name, container, image)   : fix ImagePullBackOff/bad image tag
- restart_deployment(name)                       : roll pods stuck in a bad state
- delete_pod(name)                               : recreate a single stuck pod

How to work:
1. ALWAYS inspect with the read-only tools before doing anything. Never guess.
2. Usual flow: list_pods to find the unhealthy pod and its EXACT name, then
   describe_pod to read the Events/reason, then get_pod_logs if it crashed.
3. Pod names have random suffixes (e.g. broken-app-588c94f6cc-cqjtl). Always get
   the exact name from list_pods before calling describe_pod / get_pod_logs / delete_pod.
4. State the ROOT CAUSE in plain English first. Then, if a fix tool applies, call
   the SINGLE most appropriate one. The tool will ask the human to approve, so you
   do not need to ask for permission yourself — just call it.
5. Prefer the most targeted fix (e.g. set_deployment_image for a bad tag) over a
   blunt restart. Change one thing at a time.
6. CHOOSING AN IMAGE: never substitute an unrelated image (e.g. do NOT put 'busybox'
   or 'nginx' on a web app). If the only problem is a bad TAG, keep the same image
   name with a valid tag. To find the right image, call list_deployment_images and
   reuse the image of a healthy sibling deployment running the same app. If you still
   cannot determine the correct image, DO NOT GUESS — say so and ask the user.
7. VERIFY: after a fix is applied, call wait_for_deployment(name) ONCE to confirm the
   rollout succeeded (it blocks until Ready or times out). If it times out, the fix may
   still be progressing or may be wrong — report what you see. Do NOT poll list_pods
   over and over.
8. If the user declined the fix, just report the diagnosis and the fix you proposed.
Be concise. Do not repeat the same tool call over and over.\
"""


def build_agent():
    llm = get_llm()
    from tools import _ns
    # create_react_agent builds the whole think -> call tool -> observe -> repeat
    # loop for us. MemorySaver gives the agent memory across turns in one session.
    return create_agent(
        llm,
        TOOLS,
        system_prompt=SYSTEM_PROMPT.format(namespace=_ns()),
        checkpointer=MemorySaver(),
    )


def _text_of(content) -> str:
    """Get the plain text out of a message's content. Gemini returns content as a
    LIST of blocks (e.g. [{'type': 'text', 'text': '...'}]) rather than a string, so
    we join the text of each text-block. A plain string is returned as-is."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts).strip()
    return str(content)


def run_turn(agent, user_text: str, config: dict) -> None:
    """Stream one turn, printing tool calls as they happen, then the answer."""
    final_answer = ""
    try:
        for step in agent.stream(
            {"messages": [HumanMessage(content=user_text)]},
            config,
            stream_mode="updates",
        ):
            for node, update in step.items():
                for msg in update.get("messages", []):
                    # The model decided to call a tool:
                    if getattr(msg, "tool_calls", None):
                        for tc in msg.tool_calls:
                            args = tc.get("args", {})
                            arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
                            print(f"   > {tc['name']}({arg_str})", flush=True)
                    # The model produced text (its final answer):
                    elif getattr(msg, "content", "") and msg.type == "ai":
                        final_answer = _text_of(msg.content)
    except GraphRecursionError:
        print(
            "\nAgent: I took too many steps without finishing (likely re-checking a "
            "pod that isn't ready yet). Here's where I got to — re-run the question to "
            "see the latest cluster state.\n"
        )
        if final_answer:
            print(f"Last thing I said:\n{final_answer}\n")
        return
    print(f"\nAgent: {final_answer}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Kubernetes troubleshooting agent (diagnose + approval-gated fixes)."
    )
    parser.add_argument(
        "-n", "--namespace", default=os.getenv("K8S_NAMESPACE", "default"),
        help="Kubernetes namespace to operate in (default: 'default').",
    )
    parser.add_argument(
        "question", nargs="*",
        help="A one-shot question. Omit to start interactive mode.",
    )
    args = parser.parse_args()

    # The tools read the namespace from K8S_NAMESPACE at call time, so set it before
    # building the agent (the system prompt is also stamped with this namespace).
    os.environ["K8S_NAMESPACE"] = args.namespace

    print(f"Building agent (connecting to Gemini, namespace '{args.namespace}')...", flush=True)
    agent = build_agent()
    # recursion_limit caps how many think/tool steps one turn may take — a guard
    # against the model getting stuck in a loop. A diagnose->fix->verify flow needs
    # more steps than diagnosis alone, so we allow a bit more headroom.
    config = {"configurable": {"thread_id": "session-1"}, "recursion_limit": 25}

    # One-shot mode: `python k8s_agent.py "your question"`
    if args.question:
        run_turn(agent, " ".join(args.question), config)
        return

    # Interactive mode
    print("\nKubernetes Troubleshooting Agent")
    print("Ask things like: 'what is wrong with broken-app?' / 'are all pods healthy?'")
    print("Type 'quit' to exit.\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break
        run_turn(agent, user_input, config)


if __name__ == "__main__":
    main()
