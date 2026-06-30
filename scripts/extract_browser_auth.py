#!/usr/bin/env python3
"""
Extract Povison API credentials from browser network requests
This script helps identify the authentication headers used in the web interface
"""

import re
import json
import sys
from typing import Dict, Optional

def analyze_auth_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Analyze headers to extract authentication information"""
    auth_info = {}
    
    # Look for common auth patterns
    for key, value in headers.items():
        key_lower = key.lower()
        
        if key_lower in ['appid', 'app-id', 'x-app-id']:
            auth_info['app_id'] = value
        elif key_lower in ['appkey', 'app-key', 'x-app-key']:
            auth_info['app_key'] = value
        elif key_lower in ['authorization', 'auth']:
            auth_info['auth_header'] = value
        elif key_lower == 'sign':
            auth_info['sign'] = value
        elif key_lower == 'ts':
            auth_info['timestamp'] = value
            
    return auth_info

def reverse_engineer_sign(sign: str, timestamp: str, body: str = "") -> Optional[str]:
    """
    Attempt to understand the signature format
    This is educational - you'd need the actual appKey to generate valid signatures
    """
    print(f"\n🔍 Signature Analysis:")
    print(f"   Sign value: {sign[:50]}..." if len(sign) > 50 else f"   Sign value: {sign}")
    print(f"   Timestamp: {timestamp}")
    
    # The signature is Base64 encoded HMAC-SHA256
    # Format: Base64(HMAC-SHA256(appKey, "{appId}:{data64}:{ts}"))
    
    # We can verify the format but cannot reverse without appKey
    try:
        import base64
        decoded = base64.b64decode(sign)
        print(f"   Decoded length: {len(decoded)} bytes")
        print(f"   Format appears to be: Base64(HMAC-SHA256)")
    except Exception as e:
        print(f"   Decoding error: {e}")
    
    return None

def generate_extraction_guide() -> str:
    """Generate step-by-step guide for browser credential extraction"""
    guide = """
🔐 Browser Authentication Extraction Guide
==========================================

Method 1: Browser Developer Tools (Recommended)
------------------------------------------------
1. Open the Povison quality check webpage in Chrome/Firefox
2. Press F12 to open Developer Tools
3. Go to the "Network" tab
4. Refresh the page or perform a query
5. Look for requests to: sodaapi.povison-inc.com
6. Click on the API request
7. Check the "Headers" section

Look for these headers:
- appId (or X-App-Id)
- appKey (or X-App-Key) 
- sign
- ts (timestamp)

Method 2: Browser Console
--------------------------
1. Open Developer Tools (F12)
2. Go to "Console" tab
3. Run this JavaScript code:

   // Intercept network requests
   const originalFetch = window.fetch;
   window.fetch = function(...args) {
       console.log('Fetch request:', args[0]);
       return originalFetch.apply(this, args).then(response => {
           console.log('Response headers:', [...response.headers.entries()]);
           return response;
       });
   };

4. Perform a query on the webpage
5. Check console for intercepted requests

Method 3: Page Source Analysis
-------------------------------
1. Right-click on the page → "View Page Source"
2. Search for: appId, appKey, API_KEY, etc.
3. Look in JavaScript files and configuration objects

Method 4: Local Storage/Session Storage
---------------------------------------
1. Developer Tools → Application tab
2. Local Storage → Look for auth-related keys
3. Session Storage → Check for temporary credentials

🚨 Security Notes:
- These credentials are sensitive - handle them carefully
- Only use them for legitimate business purposes
- Don't share them outside your organization
- Consider asking IT team for official API access

📝 After Extraction:
Once you find the credentials, add them to:
~/.hermes/profiles/povison-cs/.env

export POVISON_SODA_API_ID='your-found-app-id'
export POVISON_SODA_API_KEY='your-found-app-key'
"""
    return guide

def main():
    print(generate_extraction_guide())
    
    # If user provides captured data, we can help analyze it
    if len(sys.argv) > 1:
        print("\n📋 Analyzing provided data...")
        # Add analysis logic for user-provided data

if __name__ == "__main__":
    main()
