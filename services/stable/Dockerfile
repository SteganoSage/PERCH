# PERCH Service Dockerfile
# Used for both stable and canary services (identical code, different env vars)

FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the service application
COPY app.py .

# Expose the service port
EXPOSE 8000

# Run with uvicorn (production ASGI server)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
