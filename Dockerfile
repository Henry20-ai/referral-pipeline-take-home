# Use an official, slim Python image (small download, fewer moving parts).
FROM python:3.12-slim

# Python settings: no .pyc files, and print logs immediately (useful in `docker run`).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    OUTPUT_DIR=/app/output \
    PROFILING_DIR=/app/profiling

# All commands below run from /app inside the container.
WORKDIR /app

# Copy the requirements file first and install dependencies.
# (Docker caches this layer, so rebuilds are fast unless requirements.txt changes.)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code. The CSV data and the results are NOT baked into the
# image: data is mounted in read-only and results are written to a mounted folder,
# so the report ends up outside the container (see README.md).
COPY src/ ./src/

# Default command: profile all tables, then build the referral report.
CMD ["sh", "-c", "python src/profile_tables.py && python src/referral_pipeline.py"]
