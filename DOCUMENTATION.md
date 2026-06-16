# DevOps — Kubernetes Troubleshooting Agent — Documentation

This folder is a **DevOps + AI learning project**. It does three things that work together:

1. **Builds a tiny app** (`app/`) and packages it as a Docker image.
2. **Deploys it to Kubernetes** (`k8s/`) — including one deployment that is **broken on
   purpose**.
3. **Runs an AI agent** (`agent/`) that inspects the cluster and explains, in plain
   English, what went wrong and how to fix it.

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
│   ├── llm.py               #   get_llm() — returns the Gemini model
│   ├── tools.py             #   5 read-only kubectl tools
│   └── k8s_agent.py         #   LangGraph agent + terminal chat
└── venv/                    # Python environment (gitignored)
```

How the pieces connect: **`app`** is the container we ship to **Kubernetes** using the
manifests in **`k8s`**. One manifest is broken so there is a realistic failure to debug.
The **`agent`** is an AI assistant whose only job is to look at that cluster and diagnose
the problem using read-only `kubectl` commands.

---

## Part 1 — `app/` (The service we deploy)

### What is used and why

| Thing | Technology | Why |
|-------|-----------|-----|
| The service | **FastAPI** (`app/main.py`) | Deliberately tiny — just `/` and `/health`. The point isn't the app, it's having *a real container running in Kubernetes* for the agent to inspect. |
| Web server | **Uvicorn** | ASGI server that runs the FastAPI app. |
| Packaging | **Docker** (`app/Dockerfile`) | Builds the service into an image, `demo-web-app:v1`. Copies `requirements.txt` first so dependency layers cache and rebuilds stay fast. |

The `/health` endpoint matters: Kubernetes pings it to decide whether the container is
alive and ready to receive traffic.

---

## Part 2 — `k8s/` (The Kubernetes manifests)

A **manifest** is a YAML file describing what you want Kubernetes to run. There are three:

- **`deployment.yaml`** — the *healthy* app. A **Deployment** keeps 1 pod running and
  restarts it if it dies. It uses the `/health` endpoint for two probes:
  - **readinessProbe** — "is it ready to receive traffic yet?"
  - **livenessProbe** — "is it still alive? restart it if not."
  - It also sets **resource requests/limits** (memory/CPU) so one pod can't eat all the
    RAM — important on a 7.7 GB laptop.
- **`service.yaml`** — a **Service** gives the pods a stable internal name and IP and
  load-balances traffic to them. (Pods get a new IP every restart; the Service stays put.)
  It listens on port 80 and forwards to the container's port 8000.
- **`broken-deployment.yaml`** — **broken on purpose.** It points at the image tag
  `demo-web-app:v99-doesnotexist`, which was never built. Kubernetes can't pull it, so the
  pod gets stuck in **`ImagePullBackOff`** — one of the most common real-world K8s
  failures. **This is the problem the AI agent is built to diagnose.**

---

## Part 3 — `agent/` (The AI agent) ⭐

An AI agent that **diagnoses Kubernetes problems in plain English**. You ask it what's
wrong; it inspects the cluster with read-only `kubectl` commands and explains the root
cause and a fix.

```
You: what is wrong with broken-app?
   > describe_pod(pod_name='broken-app-...')
Agent: broken-app is in ImagePullBackOff. The image "demo-web-app:v99-doesnotexist"
       doesn't exist, so Kubernetes can't pull it. Fix: correct the image tag.
```

### What is used and why

| File | Technology | What it does / why |
|------|-----------|--------------------|
| `llm.py` | **Google Gemini** via **langchain-google-genai** | The "brain." A single `get_llm()` factory returns a `ChatGoogleGenerativeAI` model (default `gemini-2.5-flash`). Reads your `GOOGLE_API_KEY` from the environment. |
| `tools.py` | **LangChain `@tool`** | Five **read-only** kubectl wrappers: `list_pods`, `describe_pod`, `get_pod_logs`, `get_events`, `list_deployments`. Each function's docstring is the instruction the model reads to decide *when* to call it. |
| `k8s_agent.py` | **LangGraph** (`create_agent`) + **MemorySaver** | Builds the agent loop and the terminal chat. |

### Why wrap `kubectl` in tools instead of just running `kubectl`?

You, a human, should still use `kubectl get pods` directly — it's faster. The wrappers
exist for the **model**, not you. They add four things a raw shell command can't give an LLM:

1. **A docstring** the model reads to know when to use the tool.
2. **A string return value** the model can read back into its reasoning (a shell command
   prints to *your* terminal — the model can't see that).
3. **Guardrails** — only `get`/`describe`/`logs` tools exist, so the agent physically
   *cannot* `delete` or `apply`. It can observe the cluster but never change it.
4. **Output limits + error handling** — `describe` output is truncated to fit the model's
   context window, and `kubectl not found`/timeouts become clean text instead of crashes.

### How it works (the loop)

1. **Tools** (`tools.py`) — each runs one read-only `kubectl` command and returns text.
2. **The agent** (`k8s_agent.py`) — LangGraph's `create_agent` builds the cycle:
   **think → call a tool → read the result → repeat → final answer.**
3. **Memory** — `MemorySaver` keeps the conversation, so follow-up questions have context.
4. **Safety** — read-only by construction (see guardrails above).
5. **Loop guard** — `recursion_limit` caps how many think/tool steps one turn may take, so
   the agent can't get stuck repeating tool calls forever.

---

## How to use the AI agent — step by step

> **Note:** The agent uses **Google Gemini** (paid API). You need a `GOOGLE_API_KEY`.

### Prerequisites

- **Docker Desktop** (running)
- **minikube** + **kubectl**
- **Python 3.12+**
- A **Google Gemini API key** (from Google AI Studio)

### Step 1 — Install the Python dependencies (one time)

```bash
cd devops
py -m venv venv
./venv/Scripts/python -m pip install langgraph langchain langchain-google-genai
```

### Step 2 — Set your Gemini API key

PowerShell (Windows):
```powershell
$env:GOOGLE_API_KEY = "your-key-here"
```

Git Bash:
```bash
export GOOGLE_API_KEY="your-key-here"
```

(Optional) pick a different model — the default is `gemini-2.5-flash`:
```bash
export GEMINI_MODEL="gemini-2.5-pro"
```

### Step 3 — Start the cluster and deploy the apps

```bash
# Start Kubernetes
minikube start

# Build the demo image INSIDE minikube's Docker (Windows: `minikube image load`
# is broken, so build directly in minikube's Docker daemon)
cd app
eval $(minikube -p minikube docker-env --shell bash)   # Git Bash
docker build -t demo-web-app:v1 .
cd ..

# Deploy the healthy app + its Service
kubectl apply -f k8s/deployment.yaml -f k8s/service.yaml

# Deploy the intentionally broken app (the agent's practice problem)
kubectl apply -f k8s/broken-deployment.yaml

# Confirm:
kubectl get pods
#   web-app-...     1/1   Running
#   broken-app-...  0/1   ImagePullBackOff
```

### Step 4 — Run the agent

```bash
cd agent

# Interactive chat (remembers context across questions in a session)
../venv/Scripts/python k8s_agent.py

# Or ask one question and exit:
../venv/Scripts/python k8s_agent.py "what is wrong with broken-app?"
```

Things to ask it:
- `what is wrong with broken-app?`
- `are all my pods healthy?`
- `show me the logs for web-app`
- `which deployments are not fully available?`

### Step 5 — Clean up when done

```bash
kubectl delete -f k8s/      # remove the apps
minikube stop               # stop the cluster (keeps it for next time)
# minikube delete           # or wipe it entirely
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `minikube image load` fails with `wmic ... not found` | Known Windows 11 bug. Build inside minikube's Docker instead (the `docker-env` trick in Step 3). |
| Healthy app stuck in `ImagePullBackOff` | The image isn't in minikube's Docker. Rebuild with the `docker-env` trick. |
| `GOOGLE_API_KEY` not set / auth error | Make sure you exported the key in the **same terminal** you run the agent from. |
| `ModuleNotFoundError: langchain_google_genai` | Run the pip install in Step 1 against the venv. |
| Agent answers without calling tools | Ask a more specific question (e.g. name the pod), or check the model has tool-calling enabled. |
