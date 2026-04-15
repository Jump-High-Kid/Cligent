#!/bin/bash

# 최신 설정 파일 sync
mkdir -p ~/claude-sync/configs/agents
cp ~/.claude/CLAUDE.md ~/claude-sync/configs/
cp ~/.claude/instincts.md ~/claude-sync/configs/
cp ~/.claude/agents/*.md ~/claude-sync/configs/agents/
cp ~/Library/Application\ Support/Claude/claude_desktop_config.json ~/claude-sync/configs/

# GitHub 백업
cd ~/claude-sync
git add .
git commit -m "자동 백업: $(date '+%Y-%m-%d %H:%M')"
git push
