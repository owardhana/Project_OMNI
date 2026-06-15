FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The build context is ./backend, so the package is copied to /app and the
# app is imported as a top-level package.
COPY . /app/backend

ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
