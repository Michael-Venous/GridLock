# Gridlock alert service for AWS Lambda: the data pipeline (Python + poppler's pdftotext) and the alert handlers.
# Build from the repository root:  docker build --platform linux/amd64 --provenance=false -f backend/Dockerfile .
FROM public.ecr.aws/lambda/python:3.12
RUN dnf install -y poppler-utils && dnf clean all
COPY backend/alerts.py backend/api.py backend/store.py backend/worker.py ${LAMBDA_TASK_ROOT}/
COPY pipeline/ ${LAMBDA_TASK_ROOT}/app/pipeline/
COPY data/filings.json data/overrides.json data/starter_projects.json ${LAMBDA_TASK_ROOT}/app/data/
COPY data/cache/ ${LAMBDA_TASK_ROOT}/app/data/cache/
CMD ["worker.handler"]
