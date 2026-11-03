FROM python:3.11-slim AS generate
WORKDIR /src
RUN pip install --no-cache-dir grpcio-tools==1.71.0
COPY proto .
RUN python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. flight.proto

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=generate /src/*_pb2*.py /app/
COPY app /app/app
