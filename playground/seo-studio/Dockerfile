# POVISON SEO Studio Bridge image (uvicorn).
# Built on the production host from /opt/povison-seo/seo-studio.
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Code is also bind-mounted at runtime; COPY gives a working image without mount.
COPY . /app

ENV PYTHONUNBUFFERED=1
EXPOSE 8766

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8766"]
