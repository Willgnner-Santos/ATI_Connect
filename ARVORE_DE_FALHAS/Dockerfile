FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-por \
    tesseract-ocr-eng \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.lock é gerado por:
#   docker run --rm -v $(pwd):/src python:3.12-slim \
#     sh -c "pip install pip-tools && pip-compile --generate-hashes /src/requirements.in -o /src/requirements.lock"
# O lock deve ser commitado e regenerado a cada mudança em requirements.in.
COPY requirements.lock requirements.in ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .

EXPOSE 8000

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
