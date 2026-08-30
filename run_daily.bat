@echo off
rem 每日自动运行: 行业观测数据构建 (任务计划程序 06:00 调用)
cd /d "%~dp0"
echo [%date% %time%] 开始运行 >> run_daily.log
python Industry_Data.py >> run_daily.log 2>&1
echo [%date% %time%] 运行结束 (exit=%errorlevel%) >> run_daily.log
