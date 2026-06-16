# Kubernetes Troubleshooting Agent

An AI agent that **diagnoses Kubernetes problems in plain English**. Ask it what's
wrong with your cluster and it inspects pods with read-only `kubectl` tools, then
explains the root cause and a fix.

```
You: what is wrong with broken-app?
   > list_pods()
   > describe_pod(pod_name='broken-app-588c94f6cc-cqjtl')
Agent: broken-app is in ImagePullBackOff. The image "demo-web-app:v99-doesnotexist"
       doesn't exist, so Kubernetes can't pull it. Fix: correct the image tag.
```

Built with **LangGraph** + **Google Gemini** (`gemini-2.5-flash` by default).

---

## What's in here

```
devops/
├── app/                     # a tiny FastAPI service we deploy to the cluster
│   ├── main.py              #   / and /health endpoints
│   ├── Dockerfile
│   └── requirements.txt
├── k8s/                     # Kubernetes manifests
│   ├── deployment.yaml      #   healthy web-app
│   ├── service.yaml         #   stable network name for web-app
│   └── broken-deployment.yaml  # intentionally broken (bad image tag)
├── agent/                   # the troubleshooting agent
│   ├── llm.py               #   get_llm() — Google Gemini factory
│   ├── tools.py             #   5 read-only kubectl tools
│   ├── k8s_agent.py         #   LangGraph agent + terminal chat
│   └── .env.example         #   copy to .env and add your GOOGLE_API_KEY
└── venv/                    # Python environment (gitignored)
```

---

## Prerequisites

- **Docker Desktop** (running)
- **minikube** + **kubectl**
- **A Google Gemini API key** — get one free at https://aistudio.google.com/apikey
- **Python 3.12+**

---

## Setup from scratch

```bash
# 0. Python deps
py -m venv venv
./venv/Scripts/python -m pip install langgraph langchain langchain-google-genai python-dotenv

# 1. Start the cluster
minikube start

# 2. Build the demo image INSIDE minikube's Docker
#    (on Windows, `minikube image load` is broken — this avoids it)
cd app
eval $(minikube -p minikube docker-env --shell bash)   # Git Bash
docker build -t demo-web-app:v1 .
cd ..

# 3. Deploy the healthy app
kubectl apply -f k8s/deployment.yaml -f k8s/service.yaml

# 4. Deploy the intentionally broken app (the agent's practice problem)
kubectl apply -f k8s/broken-deployment.yaml

# 5. Confirm the setup
kubectl get pods
#   web-app-...     1/1   Running
#   broken-app-...  0/1   ImagePullBackOff
```

---

## Configure the API key

Copy the example file and paste in your real key:

```bash
cd agent
cp .env.example .env
# then edit .env and set GOOGLE_API_KEY=your-real-key
```

The agent loads `.env` automatically, so you set the key **once** and it works in
every terminal. (`.env` is gitignored — it will never be committed.)

Alternatively, export it for the current terminal:

```bash
export GOOGLE_API_KEY="your-key-here"     # Git Bash
$env:GOOGLE_API_KEY = "your-key-here"     # PowerShell
```

---

## Run the agent

```bash
cd agent

# Interactive chat (remembers context across questions in a session)
../venv/Scripts/python k8s_agent.py

# Or ask one question and exit
../venv/Scripts/python k8s_agent.py "what is wrong with broken-app?"
```

Things to ask:
- `what is wrong with broken-app?`
- `are all my pods healthy?`
- `show me the logs for web-app`
- `which deployments are not fully available?`

---

## Choosing a Gemini model

The agent defaults to `gemini-2.5-flash` (fast and cheap). To use a stronger model,
set the `GEMINI_MODEL` env var (or add it to `.env`):

```bash
export GEMINI_MODEL=gemini-2.5-pro
../venv/Scripts/python k8s_agent.py
```

**Nothing in the agent code changes** — that's the point of the `get_llm()` factory
in `agent/llm.py`.

---

## How it works

1. **Tools** (`tools.py`) — each is a plain Python function wrapped in `@tool` that runs
   one read-only `kubectl` command. The docstring tells the model when to use it.
2. **The agent** (`k8s_agent.py`) — LangGraph's `create_agent` builds the loop:
   think → call a tool → read the result → repeat → answer.
3. **Memory** — `MemorySaver` keeps the conversation so you can ask follow-ups.
4. **Safety** — every tool is `get`/`describe`/`logs` only. The agent can observe the
   cluster but can never `delete`/`apply`/`edit` it.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `minikube image load` fails with `wmic ... not found` | Known Windows 11 bug. Build inside minikube's Docker instead: `eval $(minikube docker-env --shell bash)` then `docker build`. |
| Pod stuck `ImagePullBackOff` for the *healthy* app | The image isn't in minikube's Docker. Rebuild with the `docker-env` trick above. |
| `GOOGLE_API_KEY` not set / auth error | Make sure `.env` has your key, or that you exported it in the **same terminal** you run the agent from. |
| `kubectl not found` | Ensure minikube/kubectl are installed and on your PATH. |

---

## Clean up

```bash
kubectl delete -f k8s/                 # remove the apps
minikube stop                          # stop the cluster (keeps it for next time)
# minikube delete                      # or wipe it entirely
```
