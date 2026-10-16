FROM node:25-alpine

# The upstream node:20-alpine tag can lag a patch release behind Alpine's own
# package index (e.g. openssl) - pull current packages at build time instead
# of waiting on the next upstream image rebuild.
RUN apk update && apk upgrade --no-cache

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]
