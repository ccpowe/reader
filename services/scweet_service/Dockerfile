FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/scweet

WORKDIR /srv/scweet-service

COPY scweet /opt/scweet
COPY scweet_service/requirements.txt ./requirements.txt
COPY scweet_service/app.py ./app.py

RUN pip install --no-cache-dir -r requirements.txt /opt/scweet

RUN useradd --create-home --uid 10001 collector \
    && mkdir -p /var/lib/scweet \
    && chown -R collector:collector /var/lib/scweet /srv/scweet-service

USER collector
EXPOSE 8090

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
