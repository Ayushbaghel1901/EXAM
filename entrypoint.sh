#!/bin/sh

# Wait for database to be ready
echo "Waiting for postgres..."

while ! nc -z db 5432; do
  sleep 0.1
done

echo "PostgreSQL started"

# Run seeding script (idempotent)
echo "Running database seeding..."
python seed.py

# Start the application
echo "Starting Gunicorn..."
exec gunicorn --bind 0.0.0.0:5006 app:app
