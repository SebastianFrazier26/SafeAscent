# MAINTENANCE_MODE picks the final stage: "false" serves the app, "true" serves
# the static 503 page. It is a Railway service variable, so flipping it and
# redeploying switches modes. Any other value fails the build on purpose.
ARG MAINTENANCE_MODE=false

FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL
ARG VITE_MAPBOX_TOKEN
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
ENV VITE_MAPBOX_TOKEN=$VITE_MAPBOX_TOKEN
RUN npm run build

FROM nginx:alpine AS nginx-base
RUN apk add --no-cache curl && \
    chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx /etc/nginx/conf.d && \
    touch /var/run/nginx.pid && \
    chown nginx:nginx /var/run/nginx.pid
EXPOSE 80
# /health, not /: in maintenance mode / is a deliberate 503.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl --fail --silent http://localhost:80/health || exit 1
CMD ["nginx", "-g", "daemon off;"]

FROM nginx-base AS site-false
COPY --chown=nginx:nginx nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx --from=builder /app/dist /usr/share/nginx/html
USER nginx

FROM nginx-base AS site-true
COPY --chown=nginx:nginx maintenance/nginx.conf /etc/nginx/conf.d/default.conf
COPY --chown=nginx:nginx maintenance/index.html /usr/share/nginx/maintenance/index.html
USER nginx

FROM site-${MAINTENANCE_MODE}
