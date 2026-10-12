FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home bridge && mkdir -p /data && chown bridge:bridge /data
USER bridge
ENV BRIDGE_DATA_DIR=/data GARMIN_TOKEN_DIR=/data/tokens BRIDGE_IN_CONTAINER=1
ENTRYPOINT ["garmin-intervals-bridge"]
CMD ["sync"]
