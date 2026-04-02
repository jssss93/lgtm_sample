FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir httpx

COPY run.py .

CMD ["python", "-u", "run.py"]
