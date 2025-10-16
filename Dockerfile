FROM python:3.11-slim

RUN apt-get update \
 && apt-get dist-upgrade -y --no-install-recommends \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

RUN mkdir -p /app/build
COPY build/index.db /app/build/index.db
ENV DB_PATH=/app/build/index.db

COPY main.py /app

EXPOSE 8000

# SSE MCP over HTTP
ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
