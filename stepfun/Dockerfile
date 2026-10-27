# ---------- build frontend ----------
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- runtime ----------
FROM node:20-bookworm-slim
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# backend deps (backend/.npmrc sets legacy-peer-deps for the seekdb package)
COPY backend/package.json backend/package-lock.json backend/.npmrc ./
RUN npm ci --omit=dev

COPY backend/ ./
COPY --from=frontend-build /build/frontend/dist /app/frontend-dist

# embedding model files, served locally to the browser
COPY scripts/download-models.sh /tmp/download-models.sh
RUN MODEL_DIR=/app/public/models/Xenova/bge-small-zh-v1.5 bash /tmp/download-models.sh

ENV FRONTEND_DIST=/app/frontend-dist
ENV PORT=3001
EXPOSE 3001
CMD ["node", "server.js"]
