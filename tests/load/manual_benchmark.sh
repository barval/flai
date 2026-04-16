#!/bin/bash
# Quick Manual Benchmark for AI Modules
# Run each command separately to measure real execution time

echo "======================================"
echo "FLAI AI Module Quick Benchmark"
echo "======================================"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

ITERATIONS=3

echo ""
echo "Test 1: Chat (Fast model - Qwen3-4B)"
echo "-----------------------------------"
for i in $(seq 1 $ITERATIONS); do
    echo -n "Iteration $i: "
    time curl -s -X POST http://localhost:5000/api/chat \
        -H "Content-Type: application/json" \
        -d '{"message":"Привет","session_id":null}' | \
        jq -r '.response' 2>/dev/null | head -c 50
    echo ""
done

echo ""
echo "Test 2: Reasoning (Slow model - gpt-oss)"
echo "----------------------------------------"
for i in $(seq 1 $ITERATIONS); do
    echo -n "Iteration $i: "
    time curl -s -X POST http://localhost:5000/api/chat \
        -H "Content-Type: application/json" \
        -d '{"message":"Сколько будет 2+2?","session_id":null,"use_reasoning":true}' | \
        jq -r '.response' 2>/dev/null | head -c 50
    echo ""
done

echo ""
echo "Test 3: Image Generation (Z-image-turbo)"
echo "----------------------------------------"
for i in $(seq 1 $ITERATIONS); do
    echo -n "Iteration $i: "
    time curl -s -X POST http://localhost:5000/api/image/generate \
        -H "Content-Type: application/json" \
        -d '{"prompt":"кот"}' | \
        jq -r '.image_url // .error' 2>/dev/null | head -c 50
    echo ""
done

echo ""
echo "======================================"
echo "Manual benchmarks completed"
echo "======================================"