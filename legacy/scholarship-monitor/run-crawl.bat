@echo off
cd /d "C:\Projects\scholarship-monitor\scholarship-monitor"
node src/index.js --once >> data\crawl.log 2>&1
