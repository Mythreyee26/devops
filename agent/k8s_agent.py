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

import sys

from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage

from llm import get_llm
from tools import TOOLS

SYSTEM_PROMPT = """\
You are a Kubernetes troubleshooting assistant for a local minikube cluster.
All resources are in the "default" namespace.

Your read-only tools:
- list_pods            : list pods and their STATUS
- describe_pod(name)   : detailed info + Events explaining WHY a pod is unhealthy
- get_pod_logs(name)   : recent container logs (for crashes)
- get_events           : recent cluster events
- list_deployments     : deployments and replica counts

How to work:
1. ALWAYS inspect the cluster with tools before answering. Never guess.
2. Usual flow: call list_pods to find the unhealthy pod and its EXACT name,
   then describe_pod to read the Events/reason, then get_pod_logs if it crashed.
3. Pod names have random suffixes (e.g. broken-app-588c94f6cc-cqjtl). Always get
   the exact name from list_pods before calling describe_pod or get_pod_logs.
4. Once you find the problem, answer with:
   - the pod and its status
   - the ROOT CAUSE in plain English
   - a concrete FIX
Be concise. Do not repeat the same tool call over and over.\
"""


def build_agent():
    llm = get_llm()
    # create_react_agent builds the whole think -> call tool -> observe -> repeat
    # loop for us. MemorySaver gives the agent memory across turns in one session.
    return create_agent(
        llm,
        TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
    )


def run_turn(agent, user_text: str, config: dict) -> None:
    """Stream one turn, printing tool calls as they happen, then the answer."""
    final_answer = ""
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
                    final_answer = msg.content
    print(f"\nAgent: {final_answer}\n")


def main():
    print("Building agent (connecting to Gemini)...", flush=True)
    agent = build_agent()
    # recursion_limit caps how many think/tool steps one turn may take — a guard
    # against a small model getting stuck in a loop.
    config = {"configurable": {"thread_id": "session-1"}, "recursion_limit": 15}

    # One-shot mode: `python k8s_agent.py "your question"`
    if len(sys.argv) > 1:
        run_turn(agent, " ".join(sys.argv[1:]), config)
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
