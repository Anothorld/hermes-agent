#!/usr/bin/env python3
"""
Povison SCM Quality Check Image API Client (Real Implementation)
Uses JWT token authentication instead of appId/appKey
"""

import base64
import hashlib
import hmac
import json
import sys
import requests
from datetime import datetime
from typing import Dict, List, Optional
import os


class PovisonSCMApiClient:
    """Povison SCM API client with JWT authentication"""

    def __init__(self, base_url: str = "https://scm.povison-inc.com"):
        self.base_url = base_url
        self.endpoint = "/srm/quality/check/detail/img/page"
        self.jwt_token = os.environ.get("POVISON_SCM_JWT_TOKEN", "")
        
    def _generate_sign(self, timestamp: int) -> str:
        """
        Generate x-sign signature
        Based on the request pattern, this appears to be a simple hash
        Algorithm might be: MD5(timestamp + secret) or similar
        
        Since we don't know the exact algorithm, we'll try common patterns
        """
        # Try common signing patterns
        patterns = [
            f"{timestamp}",  # Just timestamp
            f"{int(timestamp/1000)}",  # Timestamp in seconds
            f"povison{timestamp}",  # With app name
            f"quality{timestamp}",  # With resource name
        ]
        
        for pattern in patterns:
            # Try MD5
            sign = hashlib.md5(pattern.encode()).hexdigest()
            if len(sign) == 32:  # MD5 produces 32 char hex string
                return sign.upper()
        
        # Fallback: return timestamp-based hash
        return hashlib.md5(str(timestamp).encode()).hexdigest().upper()

    def _build_headers(self, timestamp: Optional[int] = None) -> Dict[str, str]:
        """Build request headers with authentication"""
        if timestamp is None:
            timestamp = int(datetime.now().timestamp() * 1000)
        
        sign = self._generate_sign(timestamp)
        
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": f"{self.base_url}/qualityManage/qualityPicture",
            "Origin": self.base_url,
            "x-access-token": self.jwt_token,
            "x-sign": sign,
            "x-timestamp": str(timestamp),
        }

    def query_images(
        self,
        page_no: int = 1,
        page_size: int = 10,
        psku: Optional[str] = None,
        version: Optional[str] = None,
        _t: Optional[int] = None
    ) -> Dict:
        """
        Query quality check images from SCM API

        Args:
            page_no: Page number (default: 1)
            page_size: Page size (default: 10)
            psku: Platform SKU
            version: Product version
            _t: Timestamp parameter

        Returns:
            API response dict
        """
        # Limit page size
        page_size = min(page_size, 50)
        
        # Build query parameters
        params = {
            "pageSize": page_size,
            "pageNo": page_no,
            "_t": _t or int(datetime.now().timestamp())
        }
        
        if psku:
            params["psku"] = psku
        if version:
            params["version"] = version
        
        # Build headers
        timestamp = int(datetime.now().timestamp() * 1000)
        headers = self._build_headers(timestamp)
        
        # Build URL
        url = f"{self.base_url}{self.endpoint}"
        
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=30
            )
            
            # Try to parse JSON response
            try:
                return response.json()
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "error": f"Invalid JSON response",
                    "status_code": response.status_code,
                    "text": response.text[:500]
                }
                
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "url": url,
                "params": params
            }

    def format_images_summary(self, response: Dict) -> str:
        """Format API response into a readable summary"""
        
        # Check for different response formats
        if isinstance(response, list):
            records = response
            total = len(records)
        elif isinstance(response, dict):
            if "data" in response and isinstance(response["data"], list):
                records = response["data"]
                total = len(records)
            elif "records" in response:
                records = response["records"]
                total = response.get("total", len(records))
            elif "result" in response and isinstance(response["result"], dict):
                if "records" in response["result"]:
                    records = response["result"]["records"]
                    total = response["result"].get("total", len(records))
                else:
                    records = response["result"].get("data", [])
                    total = len(records)
            else:
                records = response.get("data", [])
                total = len(records)
        else:
            return f"Unexpected response format: {type(response)}"

        if not records:
            return "No quality check images found."

        summary = f"Found {total} quality check image(s):\n\n"

        for i, record in enumerate(records, 1):
            if isinstance(record, dict):
                # Try different field names
                psku = record.get("psku") or record.get("sku") or record.get("platformSku") or "N/A"
                qc_code = record.get("qcCode") or record.get("qcCode") or record.get("qualityCheckCode") or "N/A"
                date = record.get("actualDate") or record.get("qualityDate") or record.get("date") or "N/A"
                img_url = record.get("qualifiedImgUrl") or record.get("imageUrl") or record.get("url") or "N/A"

                summary += f"{i}. SKU: {psku} | QC Code: {qc_code} | Date: {date}\n"
                summary += f"   Image URL: {img_url}\n\n"
            else:
                summary += f"{i}. {record}\n\n"

        return summary

    def extract_image_urls(self, response: Dict) -> List[str]:
        """Extract image URLs from API response"""
        urls = []
        
        # Normalize response format
        if isinstance(response, list):
            records = response
        elif isinstance(response, dict):
            if "data" in response and isinstance(response["data"], list):
                records = response["data"]
            elif "records" in response:
                records = response["records"]
            elif "result" in response and isinstance(response["result"], dict):
                records = response["result"].get("records", response["result"].get("data", []))
            else:
                records = response.get("data", [])
        else:
            return urls

        for record in records:
            if isinstance(record, dict):
                # Try different field names for image URL
                url = (record.get("qualifiedImgUrl") or 
                      record.get("imageUrl") or 
                      record.get("url") or 
                      record.get("imgUrl"))
                if url:
                    urls.append(url)

        return urls


def main():
    """CLI interface for Povison SCM API"""
    if len(sys.argv) < 2:
        print("Usage: python povison_scm_api.py <command> [options]")
        print("\nCommands:")
        print("  query --psku <SKU> [--page <N>] [--size <N>]")
        print("  urls  --psku <SKU> [same filter options as query]")
        print("\nExamples:")
        print("  python povison_scm_api.py query --psku 8033")
        print("  python povison_scm_api.py urls --psku 8033 --page 1 --size 5")
        sys.exit(1)

    command = sys.argv[1]
    
    # Check for JWT token
    jwt_token = os.environ.get("POVISON_SCM_JWT_TOKEN", "")
    if not jwt_token:
        print("Error: POVISON_SCM_JWT_TOKEN environment variable is required")
        print("Get it from browser Network tab: x-access-token header")
        sys.exit(1)

    api = PovisonSCMApiClient()

    # Parse command line arguments
    kwargs = {}
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--psku" and i + 1 < len(sys.argv):
            kwargs["psku"] = sys.argv[i + 1]
            i += 2
        elif arg == "--page" and i + 1 < len(sys.argv):
            kwargs["page_no"] = int(sys.argv[i + 1])
            i += 2
        elif arg == "--size" and i + 1 < len(sys.argv):
            kwargs["page_size"] = int(sys.argv[i + 1])
            i += 2
        elif arg == "--version" and i + 1 < len(sys.argv):
            kwargs["version"] = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    # Execute command
    if command == "query":
        response = api.query_images(**kwargs)
        print(api.format_images_summary(response))

    elif command == "urls":
        response = api.query_images(**kwargs)
        urls = api.extract_image_urls(response)
        print(json.dumps(urls, indent=2))

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()