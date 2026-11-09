FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY adapters adapters
COPY templates templates
ENV PORT=8000
EXPOSE 8000
# One worker + threads: job state lives in memory, so do NOT raise --workers above 1.
# --timeout 300: long enough that a slow upload (big sheet) or a slow /status
#   poll during a hot moment won't kill the request. Background batch threads
#   are independent of request lifecycle anyway.
# --graceful-timeout 30: let in-flight requests finish during a redeploy.
CMD ["gunicorn", "-w", "1", "--threads", "16", "--timeout", "300", "--graceful-timeout", "30", "-b", "0.0.0.0:8000", "app:app"]
