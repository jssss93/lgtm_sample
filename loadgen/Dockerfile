FROM python:3.12-slim

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

RUN pip install --no-cache-dir httpx

COPY run.py .

RUN chown -R app:app /app
USER app

CMD ["python", "-u", "run.py"]
