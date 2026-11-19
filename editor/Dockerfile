FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY editor/requirements.txt /app/editor/requirements.txt
RUN pip install --no-cache-dir -r editor/requirements.txt
COPY editor /app/editor
CMD ["python", "-m", "editor.worker"]
