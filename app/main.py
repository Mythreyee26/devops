"""
A tiny FastAPI demo service.

This is the app we'll deploy to Kubernetes. It does almost nothing on purpose —
the point is to have a real container running in the cluster so our
troubleshooting agent has something to inspect.

Endpoints:
  GET /         -> a hello message
  GET /health   -> {"status": "ok"}   (Kubernetes uses this kind of endpoint
                                        to check if the app is alive)
"""

from fastapi import FastAPI

app = FastAPI(title="demo-web-app")


@app.get("/")
def root():
    return {"message": "Hello from the demo web app!"}


@app.get("/health")
def health():
    # A "health check" endpoint. Kubernetes can ping this to decide whether
    # the container is healthy. If it stops returning 200, K8s can restart it.
    return {"status": "ok"}
