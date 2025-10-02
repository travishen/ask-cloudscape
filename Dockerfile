FROM python:3.11-slim

RUN apt-get update \
 && apt-get dist-upgrade -y --no-install-recommends \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/ /app/app/
COPY build/index.db /app/index.db

ENV DB_PATH=/app/index.db

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
