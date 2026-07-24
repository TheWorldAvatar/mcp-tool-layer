# mini_marie: MOF + TWA city MCP servers, cached workflow engines
FROM python:3.12-slim-bookworm

WORKDIR /app

# shapely wheels may need libgeos at runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgeos-c1v5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-docker.txt requirements-gui.txt requirements-kgqa.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt -r requirements-gui.txt -r requirements-kgqa.txt

COPY mini_marie ./mini_marie
COPY models ./models
COPY src/utils ./src/utils
COPY configs ./configs
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/data/log /app/raw_data

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV MINI_MARIE_DATA_DIR=/app/data

# SQLite caches + optional workflow recordings
VOLUME ["/app/data"]

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "mini_marie.mop_mof.mof.main"]
