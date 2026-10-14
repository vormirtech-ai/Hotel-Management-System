FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HMS_HOST=0.0.0.0 HMS_PORT=8000 HMS_DATA_DIR=/data TZ=Asia/Kolkata
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY run.py .
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "run.py"]
