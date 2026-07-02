FROM python:3.10-slim

# Install system dependencies required for OpenCV and building packages
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Ensure the backend static directories exist
RUN mkdir -p /app/backend/static/uploads /app/backend/static/outputs

# Expose FastAPI port
EXPOSE 8000

# Run the FastAPI server
CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "8000"]
