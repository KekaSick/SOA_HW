FROM eclipse-temurin:17-jre
ARG GENERATOR_VERSION=7.10.0
RUN curl -fsSL --retry 4 --retry-all-errors --max-time 90 https://repo.maven.apache.org/maven2/org/openapitools/openapi-generator-cli/${GENERATOR_VERSION}/openapi-generator-cli-${GENERATOR_VERSION}.jar -o /generator.jar
WORKDIR /work
ENTRYPOINT ["java", "-jar", "/generator.jar"]
