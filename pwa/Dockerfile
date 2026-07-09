# Production web image: build the PWA, serve it with nginx (which also
# proxies /api and serves uploaded media from the shared volume).
# Built by CI with the REPO ROOT as context (docker build -f pwa/Dockerfile .)
# so it shares infra/nginx/nginx.conf with the local compose stack.
# Pulled by the NAS — never built there.

# --platform=$BUILDPLATFORM: the PWA build runs natively on the CI runner
# (dist/ is architecture-independent static files); only the nginx stage
# below is built per target platform. Without this, arm64 image builds
# run node under QEMU emulation and take ~10x longer.
FROM --platform=$BUILDPLATFORM node:22-alpine AS build

WORKDIR /app
COPY pwa/package.json pwa/package-lock.json ./
RUN npm ci

COPY pwa/ .
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY infra/nginx/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
