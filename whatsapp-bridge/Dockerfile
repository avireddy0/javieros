FROM node:20-slim

RUN apt-get update && apt-get install -y git ca-certificates --no-install-recommends && rm -rf /var/lib/apt/lists/*
RUN git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"

WORKDIR /app

COPY package.json .
RUN npm install --omit=dev

COPY index.js .

RUN mkdir -p /data/whatsapp-session

EXPOSE 3000
CMD ["node", "index.js"]
