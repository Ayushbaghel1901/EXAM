# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install system dependencies for psycopg2 and other packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Install gunicorn for production-ready serving
RUN pip install gunicorn

# Copy project
COPY . .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Expose the port the app runs on
EXPOSE 5006

# Use entrypoint script
ENTRYPOINT ["./entrypoint.sh"]
