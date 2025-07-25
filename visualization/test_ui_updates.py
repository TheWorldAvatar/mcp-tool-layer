#!/usr/bin/env python3
"""
Test UI Updates
Simple test to verify that the visualization UI updates work correctly.
"""

import os
import sys
import time
import requests
import json
from datetime import datetime

# Add parent directory to path
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parent_dir)

def test_ui_updates():
    """Test the UI update flow."""
    base_url = "http://localhost:5000"
    
    print("🧪 Testing UI Updates...")
    
    # Test 1: Check if server is running
    try:
        response = requests.get(f"{base_url}/api/pipeline_state")
        print(f"✅ Server is running - Status: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ Server is not running. Please start the server first.")
        return False
    
    # Test 2: Test API endpoints
    endpoints = [
        "/api/pipeline_state",
        "/api/resources", 
        "/api/logs",
        "/api/get_conversation",
        "/api/agent_status"
    ]
    
    for endpoint in endpoints:
        try:
            response = requests.get(f"{base_url}{endpoint}")
            print(f"✅ {endpoint} - Status: {response.status_code}")
        except Exception as e:
            print(f"❌ {endpoint} - Error: {e}")
    
    # Test 3: Test pipeline start
    try:
        payload = {
            "task_name": "test_task",
            "meta_instruction": "Test instruction"
        }
        response = requests.post(
            f"{base_url}/api/start_pipeline",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        print(f"✅ Start pipeline - Status: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            print(f"📝 Pipeline response: {result}")
        
    except Exception as e:
        print(f"❌ Start pipeline - Error: {e}")
    
    # Test 4: Monitor for a few seconds
    print("⏱️ Monitoring for 10 seconds...")
    for i in range(5):
        try:
            # Check agent status
            response = requests.get(f"{base_url}/api/agent_status")
            if response.status_code == 200:
                status = response.json()
                print(f"📊 Agent status: {status.get('status', 'unknown')}")
            
            # Check conversation
            response = requests.get(f"{base_url}/api/get_conversation")
            if response.status_code == 200:
                conv = response.json()
                print(f"💬 Conversation messages: {len(conv.get('conversation', []))}")
            
            time.sleep(2)
        except Exception as e:
            print(f"❌ Monitoring error: {e}")
    
    print("🎉 Test completed!")
    return True

if __name__ == "__main__":
    test_ui_updates() 