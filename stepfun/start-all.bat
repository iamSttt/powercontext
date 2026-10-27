@echo off
rem 一键启动：seekdb 容器（如需）+ 后端 + 前端
echo [1/3] 检查 seekdb 容器...
docker start seekdb > nul 2>&1
timeout /t 8 /nobreak > nul

echo [2/3] 启动后端 (http://127.0.0.1:3001)...
start "stepfun-backend" cmd /k "cd /d %~dp0backend && node server.js"

timeout /t 4 /nobreak > nul

echo [3/3] 启动前端 (http://localhost:5173)...
start "stepfun-frontend" cmd /k "cd /d %~dp0frontend && npx vite"

echo.
echo 完成！浏览器打开 http://localhost:5173
