# Chat + canvas UI — build from ACE/frontend:
#   docker build -t ace/frontend .
FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Proxy /api to the ACE container (service name "ace" on the compose/CAE network).
RUN printf 'server { listen 80; root /usr/share/nginx/html; location /api { proxy_pass http://ace:3000; proxy_buffering off; } location / { try_files $uri /index.html; } }\n' > /etc/nginx/conf.d/default.conf
EXPOSE 80
