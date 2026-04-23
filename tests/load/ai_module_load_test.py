#!/usr/bin/env python3
"""
AI Module Load Testing Script

Tests all AI modules and measures execution time.
Generates a comparative report with min/avg/max times.
"""
import time
import requests
import statistics
import json
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:5000"
TEST_USER = "admin"
TEST_PASS = "admin123"  # Password set via CLI
ITERATIONS = 5  # Number of iterations per test

# Colors for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_header(text):
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}{text:^60}{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")

def print_result(name, times):
    if not times:
        print(f"  {RED}✗ FAILED - No results{RESET}")
        return
    
    min_time = min(times)
    avg_time = statistics.mean(times)
    max_time = max(times)
    
    # Determine status based on typical time thresholds
    if avg_time < 10:
        status = f"{GREEN}✓ EXCELLENT{RESET}"
    elif avg_time < 30:
        status = f"{GREEN}✓ GOOD{RESET}"
    elif avg_time < 60:
        status = f"{YELLOW}⚠ FAIR{RESET}"
    else:
        status = f"{RED}✗ SLOW{RESET}"
    
    print(f"  {name}:")
    print(f"    Min:  {min_time:>8.2f}s")
    print(f"    Avg:  {avg_time:>8.2f}s")
    print(f"    Max:  {max_time:>8.2f}s")
    print(f"    Status: {status}")
    return avg_time

def get_session():
    """Login and get session with CSRF token and cookies"""
    session = requests.Session()
    
    # First get the login page to establish session and get CSRF token
    response = session.get(f"{BASE_URL}/login")
    
    # Extract CSRF token - check both meta tag and hidden input
    import re
    csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.text)
    if not csrf_match:
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    if not csrf_match:
        raise Exception("CSRF token not found in login page")
    csrf_token = csrf_match.group(1)
    
    # Login with CSRF token and session cookies
    response = session.post(f"{BASE_URL}/login", data={
        "login": TEST_USER,
        "password": TEST_PASS,
        "csrf_token": csrf_token
    }, allow_redirects=False)
    
    if response.status_code == 302:
        return session
    print(f"Login response status: {response.status_code}")
    # Print response for debugging
    print(f"Response text: {response.text[:500] if response.text else 'Empty'}")
    raise Exception(f"Login failed: {response.status_code}")

def test_chat(session, prompt="Hello! How are you?"):
    """Test chat module - fast model"""
    times = []
    for i in range(ITERATIONS):
        start = time.time()
        resp = session.post(f"{BASE_URL}/api/chat", json={
            "message": prompt,
            "session_id": None
        }, timeout=60)
        elapsed = time.time() - start
        times.append(elapsed)
        print(f"    Iteration {i+1}: {elapsed:.2f}s - Status: {resp.status_code}")
    return times

def test_reasoning(session):
    """Test reasoning module - slow model"""
    prompt = "Calculate the factorial of 7 and explain each step"
    times = []
    for i in range(ITERATIONS):
        start = time.time()
        resp = session.post(f"{BASE_URL}/api/chat", json={
            "message": prompt,
            "session_id": None,
            "use_reasoning": True
        }, timeout=300)
        elapsed = time.time() - start
        times.append(elapsed)
        print(f"    Iteration {i+1}: {elapsed:.2f}s - Status: {resp.status_code}")
    return times

def test_image_generation(session, prompt="a cat sitting on a windowsill"):
    """Test image generation module"""
    times = []
    for i in range(ITERATIONS):
        start = time.time()
        resp = session.post(f"{BASE_URL}/api/image/generate", json={
            "prompt": prompt
        }, timeout=600)
        elapsed = time.time() - start
        times.append(elapsed)
        print(f"    Iteration {i+1}: {elapsed:.2f}s - Status: {resp.status_code}")
    return times

def test_image_analysis(session):
    """Test multimodal/image analysis"""
    # Create a simple 1x1 PNG image as base64
    import base64
    png_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    
    times = []
    for i in range(ITERATIONS):
        start = time.time()
        resp = session.post(f"{BASE_URL}/api/multimodal", json={
            "image": png_data,
            "message": "What is shown in this picture?"
        }, timeout=120)
        elapsed = time.time() - start
        times.append(elapsed)
        print(f"    Iteration {i+1}: {elapsed:.2f}s - Status: {resp.status_code}")
    return times

def test_image_editing(session):
    """Test image editing module"""
    import base64
    png_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    
    times = []
    for i in range(ITERATIONS):
        start = time.time()
        resp = session.post(f"{BASE_URL}/api/image/edit", json={
            "image": png_data,
            "prompt": "make the background brighter"
        }, timeout=900)
        elapsed = time.time() - start
        times.append(elapsed)
        print(f"    Iteration {i+1}: {elapsed:.2f}s - Status: {resp.status_code}")
    return times

def test_tts(session, text="Hello! This is a text-to-speech test."):
    """Test TTS (text-to-speech)"""
    times = []
    for i in range(ITERATIONS):
        start = time.time()
        resp = session.post(f"{BASE_URL}/api/tts", json={
            "text": text,
            "voice_gender": "male"
        }, timeout=30)
        elapsed = time.time() - start
        times.append(elapsed)
        print(f"    Iteration {i+1}: {elapsed:.2f}s - Status: {resp.status_code}")
    return times

def test_asr(session):
    """Test ASR (speech-to-text)"""
    # Use a short silent audio (minimal WAV format)
    import base64
    wav_data = "UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA="
    
    times = []
    for i in range(ITERATIONS):
        start = time.time()
        resp = session.post(f"{BASE_URL}/api/asr", json={
            "audio": wav_data
        }, timeout=60)
        elapsed = time.time() - start
        times.append(elapsed)
        print(f"    Iteration {i+1}: {elapsed:.2f}s - Status: {resp.status_code}")
    return times

def test_rag(session):
    """Test RAG (document search)"""
    times = []
    for i in range(ITERATIONS):
        start = time.time()
        resp = session.post(f"{BASE_URL}/api/rag", json={
            "query": "test",
            "session_id": None
        }, timeout=30)
        elapsed = time.time() - start
        times.append(elapsed)
        print(f"    Iteration {i+1}: {elapsed:.2f}s - Status: {resp.status_code}")
    return times

def main():
    print_header("FLAI AI Module Load Testing")
    print(f"Base URL: {BASE_URL}")
    print(f"Test user: {TEST_USER}")
    print(f"Iterations per test: {ITERATIONS}")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Login
    print_header("Authenticating...")
    try:
        session = get_session()
        print(f"  {GREEN}✓ Logged in successfully{RESET}")
    except Exception as e:
        print(f"  {RED}✗ Login failed: {e}{RESET}")
        return
    
    results = {}
    
    # Test 1: Chat (Fast)
    print_header("Testing Chat (Qwen3-4B)...")
    try:
        results['chat'] = test_chat(session, "Hello! How are you?")
    except Exception as e:
        print(f"  {RED}✗ Failed: {e}{RESET}")
    
    # Test 2: Reasoning
    print_header("Testing Reasoning (gpt-oss-20b)...")
    try:
        results['reasoning'] = test_reasoning(session)
    except Exception as e:
        print(f"  {RED}✗ Failed: {e}{RESET}")
    
    # Test 3: Image Generation
    print_header("Testing Image Generation (Z-image-turbo)...")
    try:
        results['image_generation'] = test_image_generation(session)
    except Exception as e:
        print(f"  {RED}✗ Failed: {e}{RESET}")
    
    # Test 4: Image Analysis (Multimodal)
    print_header("Testing Image Analysis (Qwen3VL)...")
    try:
        results['image_analysis'] = test_image_analysis(session)
    except Exception as e:
        print(f"  {RED}✗ Failed: {e}{RESET}")
    
    # Test 5: Image Editing
    print_header("Testing Image Editing (Flux.2 Klein)...")
    try:
        results['image_editing'] = test_image_editing(session)
    except Exception as e:
        print(f"  {RED}✗ Failed: {e}{RESET}")
    
    # Test 6: TTS
    print_header("Testing TTS (Piper)...")
    try:
        results['tts'] = test_tts(session)
    except Exception as e:
        print(f"  {RED}✗ Failed: {e}{RESET}")
    
    # Test 7: ASR
    print_header("Testing ASR (Whisper)...")
    try:
        results['asr'] = test_asr(session)
    except Exception as e:
        print(f"  {RED}✗ Failed: {e}{RESET}")
    
    # Test 8: RAG
    print_header("Testing RAG (Qdrant)...")
    try:
        results['rag'] = test_rag(session)
    except Exception as e:
        print(f"  {RED}✗ Failed: {e}{RESET}")
    
    # Generate Report
    print_header("FINAL RESULTS")
    
    report = []
    report.append("=" * 80)
    report.append("FLAI AI MODULE LOAD TESTING REPORT")
    report.append("=" * 80)
    report.append(f"Test Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"Iterations per test: {ITERATIONS}")
    report.append("")
    report.append(f"{'Module':<25} {'Min (s)':<10} {'Avg (s)':<10} {'Max (s)':<10} {'Status'}")
    report.append("-" * 80)
    
    avg_times = []
    for name, times in results.items():
        if times and isinstance(times, list) and len(times) > 0:
            min_t = min(times)
            avg_t = statistics.mean(times)
            max_t = max(times)
            avg_times.append((name, avg_t))
            
            if avg_t < 10:
                status = "EXCELLENT"
            elif avg_t < 30:
                status = "GOOD"
            elif avg_t < 60:
                status = "FAIR"
            else:
                status = "SLOW"
            
            report.append(f"{name:<25} {min_t:<10.2f} {avg_t:<10.2f} {max_t:<10.2f} {status}")
    
    report.append("-" * 80)
    report.append("")
    report.append("INTERPRETATION:")
    report.append("  EXCELLENT: < 10s - Real-time interaction")
    report.append("  GOOD:      10-30s - Acceptable for async tasks")
    report.append("  FAIR:      30-60s - User may wait, consider queue")
    report.append("  SLOW:      > 60s - Heavy GPU tasks, use queue")
    report.append("")
    report.append("=" * 80)
    
    report_text = "\n".join(report)
    print(report_text)
    
    # Save to file
    with open(f"load_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt", "w") as f:
        f.write(report_text)
    
    print(f"\n{GREEN}Report saved to load_test_results_*.txt{RESET}")

if __name__ == "__main__":
    main()