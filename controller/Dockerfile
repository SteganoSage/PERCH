# PERCH Controller Dockerfile
# Runs the decision engine that observes metrics and manages traffic weights

FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY controller/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy controller code and nginx template
COPY controller/ .
COPY proxy/nginx.conf.j2 /app/nginx.conf.j2

# Run the controller (unbuffered for real-time logging)
CMD ["python", "-u", "main.py"]
