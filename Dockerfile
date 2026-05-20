FROM python:3.12-slim

WORKDIR /app

# Install deps first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

EXPOSE 5000

ENV PYTHONUNBUFFERED=1 \
    FLASK_ENV=production \
    PORT=5000

# Use gunicorn in container
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120"]
