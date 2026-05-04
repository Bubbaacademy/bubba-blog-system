#!/bin/bash
cd ~/Desktop/bubba-content-agent
source venv/bin/activate
python3 daily_runner.py >> ~/Desktop/bubba-content-agent/agent.log 2>&1
