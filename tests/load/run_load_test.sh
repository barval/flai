#!/bin/bash
# Load test runner for FLAI application
# Usage: ./run_load_test.sh [users] [spawn_rate] [duration]

USERS=${1:-5}
SPAWN_RATE=${2:-1}
DURATION=${3:-60s}
HOST="http://localhost:5000"

echo "========================================="
echo "FLAI Load Test"
echo "========================================="
echo "Users: $USERS"
echo "Spawn rate: $SPAWN_RATE/sec"
echo "Duration: $DURATION"
echo "Host: $HOST"
echo "========================================="

# Users for rotation (to avoid rate limiting)
USERS_JSON='["loaduser1","loaduser2","loaduser3","loaduser4","loaduser5"]'
PASSWORDS_JSON='["loadpass1","loadpass2","loadpass3","loadpass4","loadpass5"]'

/tmp/locust_venv/bin/locust -f tests/load/locustfile.py \
    --headless \
    -u $USERS \
    -r $SPAWN_RATE \
    --run-time $DURATION \
    --host $HOST \
    --csv=/tmp/locust_results \
    --web-loglevel=WARNING \
    --logfile=/tmp/locust.log \
    --only-summary

echo ""
echo "========================================="
echo "Test completed. Results:"
echo "========================================="

# Show summary
cat /tmp/locust_results_stats.csv 2>/dev/null | head -20

echo ""
echo "Detailed results saved to:"
echo "  - /tmp/locust_results_stats.csv"
echo "  - /tmp/locust_results_failures.csv"
echo "  - /tmp/locust.log"
