# syntax=docker/dockerfile:1
# Build context: the repository root (the image needs infra/nginx.conf).
#   docker build -f frontend/Dockerfile -t rutaviva-web .
# What enters the context is allow-listed in frontend/Dockerfile.dockerignore.

FROM node:22-alpine AS build
WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# "true" builds the demo with the in-browser mock API instead of the real one.
ARG VITE_USE_MOCKS=false
ENV VITE_USE_MOCKS=${VITE_USE_MOCKS}
RUN npm run build

FROM nginx:1.27-alpine
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -q --spider http://127.0.0.1/ || exit 1
