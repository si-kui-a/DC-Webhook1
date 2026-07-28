FROM node:18-alpine
# [WHY] 依賴（axios/cheerio/discord.js/dotenv/node-cron）全部是純 JS，
# 不需要編譯任何原生模組（better-sqlite3/sqlite3 已在改用 JSON 儲存後移除），
# 所以不需要 slim image + python3/make/g++ 這套編譯工具鏈；改用更輕量的
# alpine image（無編譯需求時通常比 slim 小上一半以上）。
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY . .
CMD ["node", "src/index.js"]
