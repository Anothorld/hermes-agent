#!/usr/bin/env python3
"""
Povison Quality Check Image API Client (Documented API Version)
Uses appId/appKey with HMAC-SHA256 authentication as per documentation
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


class PovisonQualityCheckAPI:
    """Povison Quality Check Image API with appId/appKey authentication"""

    def __init__(self, app_id: Optional[str] = None, app_key: Optional[str] = None):
        self.app_id = app_id or os.environ.get("POVISON_API_ID", "scm")
        self.app_key = app_key or os.environ.get("POVISON_API_KEY", "")
        self.base_url = "http://sodaapi.povison-inc.com/api/scm/qualityCheck/imgPage"

    def _generate_sign(self, body: str, ts: int) -> str:
        """Generate HMAC-SHA256 signature for API request
        
        Algorithm from documentation:
        1. data64 = Base64(UTF-8字节(requestBody))
        2. data = "{appId}:{data64}:{ts}"
        3. sign = Base64(HMAC-SHA256(appKey, data))
        """
        # Step 1: Base64 encode request body
        data64 = base64.b64encode(body.encode('utf-8')).decode('utf-8')

        # Step 2: Build string to sign
        data = f"{self.app_id}:{data64}:{ts}"

        # Step 3: HMAC-SHA256 and Base64 encode
        hmac_sha256 = hmac.new(
            self.app_key.encode('utf-8'),
            data.encode('utf-8'),
            hashlib.sha256
        )
        sign = base64.b64encode(hmac_sha256.digest()).decode('utf-8')

        return sign

    def _build_headers(self, body: str) -> Dict[str, str]:
        """Build request headers with authentication"""
        ts = int(datetime.now().timestamp())
        sign = self._generate_sign(body, ts)

        return {
            "Content-Type": "application/json",
            "appId": self.app_id,
            "ts": str(ts),
            "sign": sign,
        }

    def query_images(
        self,
        page_no: int = 1,
        page_size: int = 10,
        psku: Optional[str] = None,
        psku_version: Optional[str] = None,
        qc_code: Optional[str] = None,
        date_start: Optional[str] = None,
        date_end: Optional[str] = None
    ) -> Dict:
        """
        Query quality check images using documented API

        Args:
            page_no: Page number (default: 1)
            page_size: Page size, max 50 (default: 10)
            psku: Platform SKU
            psku_version: Platform SKU version
            qc_code: Quality check code
            date_start: Start date in yyyy-MM-dd format
            date_end: End date in yyyy-MM-dd format

        Returns:
            API response dict with records list
        """
        # Limit page size to max 50
        page_size = min(page_size, 50)

        # Build request body
        request_body = {
            "pageNo": page_no,
            "pageSize": page_size
        }

        if psku:
            request_body["psku"] = psku
        if psku_version:
            request_body["pskuVersion"] = psku_version
        if qc_code:
            request_body["qcCode"] = qc_code
        if date_start:
            request_body["dateStart"] = date_start
        if date_end:
            request_body["dateEnd"] = date_end

        body_json = json.dumps(request_body)
        headers = self._build_headers(body_json)

        try:
            response = requests.post(
                self.base_url,
                headers=headers,
                data=body_json,
                timeout=30
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Request failed: {str(e)}",
                "url": self.base_url,
                "body": request_body
            }

    def format_images_summary(self, response: Dict) -> str:
        """Format API response into a readable summary"""
        if not response.get("success") or response.get("code") != 200:
            error_msg = response.get("message", "Unknown error")
            return f"API Error: {error_msg}"

        result = response.get("result", {})
        records = result.get("records", [])
        total = result.get("total", 0)

        if not records:
            return "No quality check images found."

        summary = f"Found {total} quality check image(s):\n\n"

        for i, record in enumerate(records, 1):
            psku = record.get("psku", "N/A")
            qc_code = record.get("qcCode", "N/A")
            actual_date = record.get("actualDate", "N/A")
            img_url = record.get("qualifiedImgUrl", "N/A")

            summary += f"{i}. SKU: {psku} | QC Code: {qc_code} | Date: {actual_date}\n"
            summary += f"   Image URL: {img_url}\n\n"

        return summary

    def extract_image_urls(self, response: Dict) -> List[str]:
        """Extract image URLs from API response"""
        if not response.get("success") or response.get("code") != 200:
            return []

        result = response.get("result", {})
        records = result.get("records", [])

        return [record.get("qualifiedImgUrl", "") for record in records if record.get("qualifiedImgUrl")]


def main():
    """CLI interface for quality check image API"""
    if len(sys.argv) < 2:
        print("Usage: python quality_check_api.py <command> [options]")
        print("\nCommands:")
        print("  query --psku <SKU> [--version <VERSION>] [--qc-code <CODE>]")
        print("        [--date-start <YYYY-MM-DD>] [--date-end <YYYY-MM-DD>]")
        print("        [--page <N>] [--size <N>]")
        print("  urls  --psku <SKU> [same filter options as query]")
        print("\nExamples:")
        print("  python quality_check_api.py query --psku P-SKU-001")
        print("  python quality_check_api.py urls --psku P-SKU-001 --page 1 --size 5")
        sys.exit(1)

    command = sys.argv[1]
    
    # Check for credentials
    app_id = os.environ.get("POVISON_API_ID", "")
    app_key = os.environ.get("POVISON_API_KEY", "")
    
    if not app_id or not app_key:
        print("Error: POVISON_API_ID and POVISON_API_KEY environment variables are required")
        print("Current credentials configured:")
        print(f"  appId: {os.environ.get('POVISON_API_ID', 'scm')}")
        print(f"  appKey: {'***' if os.environ.get('POVISON_API_KEY') else 'NOT SET'}")
        sys.exit(1)

    api = PovisonQualityCheckAPI(app_id, app_key)

    # Parse command line arguments
    kwargs = {}
    i = 2
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--psku" and i + 1 < len(sys.argv):
            kwargs["psku"] = sys.argv[i + 1]
            i += 2
        elif arg == "--version" and i + 1 < len(sys.argv):
            kwargs["psku_version"] = sys.argv[i + 1]
            i += 2
        elif arg == "--qc-code" and i + 1 < len(sys.argv):
            kwargs["qc_code"] = sys.argv[i + 1]
            i += 2
        elif arg == "--date-start" and i + 1 < len(sys.argv):
            kwargs["date_start"] = sys.argv[i + 1]
            i += 2
        elif arg == "--date-end" and i + 1 < len(sys.argv):
            kwargs["date_end"] = sys.argv[i + 1]
            i += 2
        elif arg == "--page" and i + 1 < len(sys.argv):
            kwargs["page_no"] = int(sys.argv[i + 1])
            i += 2
        elif arg == "--size" and i + 1 < len(sys.argv):
            kwargs["page_size"] = int(sys.argv[i + 1])
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