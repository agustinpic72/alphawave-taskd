FROM node:22.17.0-alpine3.22 AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json ./
COPY frontend/public ./public
COPY frontend/src ./src

RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27.5-alpine3.21

COPY --chown=101:101 deploy/docker/nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html

USER 101:101
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
