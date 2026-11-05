FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY templates templates
ENV PORT=8000
EXPOSE 8000
# One worker + threads: job state lives in memory, so do NOT raise --workers above 1.
CMD ["gunicorn", "-w", "1", "--threads", "8", "--timeout", "120", "-b", "0.0.0.0:8000", "app:app"]
