# PERCH Load Generator Dockerfile
# Open-loop traffic generator for testing canary rollouts

FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the load generator script
COPY generate_traffic.py .

# Run the load generator (unbuffered for real-time logging)
CMD ["python", "-u", "generate_traffic.py"]
