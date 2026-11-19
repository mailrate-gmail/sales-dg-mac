FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm ci --no-audit --no-fund --registry=${NPM_REGISTRY}

COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
