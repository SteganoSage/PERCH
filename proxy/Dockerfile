# PERCH Proxy Dockerfile
# Runs nginx with weighted routing between stable and canary services

FROM nginx:1.27-alpine

# Copy bootstrap configuration (used before controller writes first config)
COPY bootstrap.conf /etc/nginx/bootstrap.conf

# Copy Jinja2 template for dynamic configuration
COPY nginx.conf.j2 /etc/nginx/nginx.conf.j2

# Copy bootstrap script to install initial config on first run
COPY 99-bootstrap.sh /docker-entrypoint.d/99-bootstrap.sh
RUN chmod +x /docker-entrypoint.d/99-bootstrap.sh
