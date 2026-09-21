FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 AS scweet-source

ARG SCWEET_SOURCE_URL=https://codeload.github.com/Altimis/Scweet/tar.gz/0192d1a74a1421c61f75c35bd7917c18d90227fd
ARG SCWEET_SOURCE_SHA256=40b87f259b62a8acff53d1764c59c8200b48addf635d48b8b7f1d07381000216

COPY scweet_service/fetch_scweet.py /tmp/fetch_scweet.py
RUN python /tmp/fetch_scweet.py "$SCWEET_SOURCE_URL" "$SCWEET_SOURCE_SHA256" /opt/scweet

FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/scweet

WORKDIR /srv/scweet-service

COPY --from=scweet-source /opt/scweet /opt/scweet
COPY scweet_service/requirements.lock ./requirements.lock
COPY scweet_service/app.py ./app.py

RUN pip install --no-cache-dir --require-hashes -r requirements.lock

RUN useradd --create-home --uid 10001 collector \
    && mkdir -p /var/lib/scweet \
    && chown -R collector:collector /var/lib/scweet /srv/scweet-service

USER collector
EXPOSE 8090

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
