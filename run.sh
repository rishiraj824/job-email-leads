#!/bin/bash
cd /Users/rishi/email-agent
echo "==============================" >> agent.log
echo "Run started: $(date)" >> agent.log
python3 agent.py >> agent.log 2>&1
echo "Run finished: $(date)" >> agent.log
