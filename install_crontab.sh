#!/bin/bash

# Create new crontab with Traycer Queue Manager jobs
cat > /tmp/traycer_crontab.txt << 'EOF'
# Traycer Queue Manager - Process queue every 32 minutes (30 min slot recharge + 2 min buffer)
*/32 * * * * /home/frankbria/projects/agentic-gh-coding/run_processor.sh >> /home/frankbria/projects/agentic-gh-coding/logs/processor.log 2>&1

# Traycer Queue Manager - Scan repos daily at 2 AM for new rate-limited issues
0 2 * * * /home/frankbria/projects/agentic-gh-coding/run_scanner.sh >> /home/frankbria/projects/agentic-gh-coding/logs/scanner.log 2>&1
EOF

# Install the crontab
crontab /tmp/traycer_crontab.txt

echo "✓ Crontab installed successfully"
echo ""
echo "Active cron jobs:"
crontab -l
