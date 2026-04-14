#!/bin/bash
cd ~/claude-sync
git add .
git commit -m "자동 백업: $(date '+%Y-%m-%d %H:%M')"
git push
