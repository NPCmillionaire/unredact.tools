# unredact.tools web app: docker build -t unredact . && docker run -p 8000:8000 unredact
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[vision,heic,web]" && useradd -r -u 10001 app
USER app
ENV UNREDACT_MAX_MB=50 UNREDACT_WORKERS=2
EXPOSE 8000
CMD ["unredact.tools", "web", "--host", "0.0.0.0", "--port", "8000"]
