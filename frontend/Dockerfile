# syntax=docker/dockerfile:1.7
# Frontend image: builds the Vite SPA, serves it via nginx, and reverse-proxies
# /api to the backend service. The same nginx layer runs in dev compose and in
# the per-customer prod deployment — keeps the surface identical.

# ---------- Stage 1: build the SPA ---------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app

# Install deps in a separate layer so package changes invalidate cache cleanly.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Copy only what's needed for the build.
COPY index.html vite.config.js ./
COPY src ./src
COPY public ./public

RUN npm run build


# ---------- Stage 2: nginx static + reverse-proxy ------------------------
FROM nginx:1.27-alpine AS runtime

# nginx in this image runs as the unprivileged "nginx" user by default for
# worker processes. We replace the default config but leave that alone.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
