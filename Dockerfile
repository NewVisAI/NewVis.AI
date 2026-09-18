FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/index.html .
COPY web_server.py .

EXPOSE 8002

CMD ["uvicorn", "web_server:app", "--host", "0.0.0.0", "--port", "8002"]
