FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system deps (if needed) and Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Default port for Render
ENV PORT=10000

# Start the app with gunicorn
CMD ["gunicorn", "BancaAtlantisBot:app", "--bind", "0.0.0.0:$PORT", "--workers", "1"]
