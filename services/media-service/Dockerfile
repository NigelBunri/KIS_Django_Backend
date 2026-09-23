# services/media-service/Dockerfile
FROM python:3.12-slim

WORKDIR /app

# rembg's onnxruntime wheel needs libgomp at runtime; nothing else in this
# image touches the OS beyond that.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

# rembg downloads its ONNX model(s) to $U2NET_HOME and caches them there.
# Pre-download the pinned u2net model INTO THE IMAGE at build time (not
# left to happen on a container's first real request) — a fresh deploy
# should never need network access, or make a real user's first request
# hang for minutes, just to fetch a model. The compose file also mounts
# this path as a volume, so a redeploy reuses the cache rather than paying
# this cost again even if the image is rebuilt from scratch.
ENV U2NET_HOME=/root/.u2net
RUN python -c "from rembg import new_session; new_session('u2net')"

EXPOSE 9000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
