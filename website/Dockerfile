# Build from the repo root:  docker build -f website/Dockerfile -t newvis-site .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY website/requirements.txt website/requirements.txt
RUN pip install --no-cache-dir -r website/requirements.txt
COPY website/ website/
RUN useradd --system --uid 10001 site && mkdir -p /data && chown site /data
USER site
ENV SITE_DB_PATH=/data/leads.db TRUST_PROXY=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["uvicorn", "website.server:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
