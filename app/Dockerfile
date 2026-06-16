# A Dockerfile is a recipe for building a container image.
# Each line is a step; Docker runs them top to bottom.

# 1. Start from a small official Python image.
FROM python:3.12-slim

# 2. Everything below happens inside /app in the container.
WORKDIR /app

# 3. Copy ONLY requirements first, then install. (Docker caches this layer, so
#    rebuilds are fast when your code changes but dependencies don't.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Now copy the app code.
COPY main.py .

# 5. Document that the app listens on port 8000.
EXPOSE 8000

# 6. The command that runs when the container starts.
#    --host 0.0.0.0 makes it reachable from outside the container.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
