#!/usr/bin/env python3
import requests

# Test submit resource page
try:
    response = requests.get('http://localhost:5000/submit-resource', allow_redirects=False)
    print(f"Status code: {response.status_code}")
    print(f"Headers: {response.headers}")
    if response.status_code == 302:
        print(f"Redirected to: {response.headers.get('Location', 'Unknown')}")
    else:
        print(f"Response text (first 200 chars): {response.text[:200]}")
except Exception as e:
    print(f"Error: {e}")