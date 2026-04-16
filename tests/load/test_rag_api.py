#!/usr/bin/env python3
"""
RAG Testing Script
Tests RAG queries with different configurations
"""
import requests
import time

BASE_URL = "http://localhost:5000"

def login():
    """Login and get session"""
    session = requests.Session()
    response = session.get(f"{BASE_URL}/login")
    
    import re
    csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.text)
    if not csrf_match:
        raise Exception("CSRF token not found")
    csrf_token = csrf_match.group(1)
    
    response = session.post(f"{BASE_URL}/login", data={
        "login": "admin",
        "password": "admin123",
        "csrf_token": csrf_token
    }, allow_redirects=False)
    
    if response.status_code == 302:
        return session
    raise Exception(f"Login failed: {response.status_code}")

def test_rag(session, query):
    """Test RAG endpoint"""
    start = time.time()
    response = session.post(f"{BASE_URL}/api/rag", json={
        "query": query,
        "session_id": None
    }, timeout=120)
    elapsed = time.time() - start
    
    try:
        data = response.json()
        return elapsed, data
    except:
        return elapsed, {"error": response.text[:200]}

def main():
    print("=" * 60)
    print("RAG Testing")
    print("=" * 60)
    
    session = login()
    print("✓ Logged in\n")
    
    # Test queries
    queries = [
        "Выведи все места работы Валерия Барсукова в Сарове",
        "Выведи все места работы Валерия Барсукова в Москве",
        "Выведи все места работы Валерия Барсукова в Нижнем Новгороде",
        "Выведи все места работы Валерия Барсукова в хронологическом порядке",
    ]
    
    for i, query in enumerate(queries, 1):
        print(f"\n--- Query {i}: {query[:50]}...")
        elapsed, result = test_rag(session, query)
        
        print(f"Time: {elapsed:.2f}s")
        
        if "error" in result:
            print(f"Error: {result.get('error', 'Unknown')[:200]}")
        else:
            # Print found chunks count
            chunks = result.get("chunks", [])
            print(f"Found {len(chunks)} chunks")
            
            # Print first few chunks as preview
            for j, chunk in enumerate(chunks[:3]):
                text = chunk.get("text", "")[:100]
                print(f"  Chunk {j+1}: {text}...")
        
        print()

if __name__ == "__main__":
    main()