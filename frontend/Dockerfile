FROM node:22-alpine AS build

WORKDIR /app

# npm ci needs the lockfile and installs devDependencies too — the build runs tsc.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# nginx serves the built bundle and fronts the API, so the browser talks to one origin and
# there is no CORS configuration to get wrong.
FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
