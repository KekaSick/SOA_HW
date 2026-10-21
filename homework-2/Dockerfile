FROM eclipse-temurin:17-jre AS generate
ARG GENERATOR_VERSION=7.10.0
RUN curl -fsSL --retry 4 --retry-all-errors --max-time 90 https://repo.maven.apache.org/maven2/org/openapitools/openapi-generator-cli/${GENERATOR_VERSION}/openapi-generator-cli-${GENERATOR_VERSION}.jar -o /generator.jar
COPY openapi /spec
COPY codegen/templates /templates
RUN java -jar /generator.jar generate -g python-fastapi -i /spec/marketplace.yaml -o /generated -t /templates --global-property apis,models,supportingFiles

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=generate /generated/src/openapi_server /app/generated/openapi_server
COPY app /app/app
COPY openapi /app/openapi
COPY static /app/static
ENV PYTHONPATH=/app/generated:/app
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
