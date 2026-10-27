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
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# backend deps (backend/.npmrc sets legacy-peer-deps for the seekdb package)
COPY backend/package.json backend/package-lock.json backend/.npmrc ./
RUN npm ci --omit=dev

# backend code + committed INT8 model files (no network needed at build/run time)
COPY backend/ ./
COPY --from=frontend-build /build/frontend/dist /app/frontend-dist

ENV FRONTEND_DIST=/app/frontend-dist
ENV PORT=3001
EXPOSE 3001
CMD ["node", "server.js"]
