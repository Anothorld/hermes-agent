#!/bin/bash
# Povison Customer Service - Quality Check Images Integration Demo
# This script demonstrates the complete workflow for handling customer image requests

set -e

echo "=== Povison Quality Check Images Integration Demo ==="
echo ""

# Example 1: Query images for a product
echo "1. Querying quality check images for product SKU..."
echo "   Command: ./scripts/qc-images query --psku P-SKU-001 --size 3"
echo ""

# Example 2: Get image URLs for programmatic use
echo "2. Getting image URLs for QuickCEP integration..."
echo "   Command: ./scripts/qc-images urls --psku P-SKU-001"
echo ""

# Example 3: Complete customer service workflow
echo "3. Complete customer service workflow:"
echo "   a) Customer asks: 'Can you show me actual photos of product P-SKU-001?'"
echo "   b) Query images: ./scripts/qc-images query --psku P-SKU-001 --size 5"
echo "   c) Present results to customer"
echo "   d) If customer wants images in email: Add to QuickCEP draft"
echo ""

# Example 4: Advanced filtering
echo "4. Advanced filtering options:"
echo "   ./scripts/qc-images query --psku P-SKU-001 --version V1 --qc-code QC-001"
echo "   ./scripts/qc-images query --psku P-SKU-001 --date-start 2024-01-01 --date-end 2024-12-31"
echo ""

# Setup instructions
echo "=== Setup Instructions ==="
echo ""
echo "1. Set required environment variables:"
echo "   export POVISON_SODA_API_ID='your-app-id'"
echo "   export POVISON_SODA_API_KEY='your-app-key'"
echo ""
echo "2. Add to ~/.hermes/profiles/povison-cs/.env for persistence"
echo ""

# Testing instructions
echo "=== Testing ==="
echo ""
echo "Test the tool without real credentials (will fail auth but show command works):"
echo "./scripts/qc-images query --psku TEST-SKU --size 1"
echo ""

echo "=== Integration Notes ==="
echo ""
echo "- API Endpoint: http://sodaapi.povison-inc.com/api/scm/qualityCheck/imgPage"
echo "- Authentication: HMAC-SHA256 with appId and appKey"
echo "- Max page size: 50 images"
echo "- Images are served from OSS with public URLs"
echo "- Can be attached to QuickCEP drafts as URL references"
echo ""

echo "=== Error Handling ==="
echo ""
echo "- If credentials not set: Script will error with clear message"
echo "- If no images found: Returns 'No quality check images found'"
echo "- Network errors: Returns error details for troubleshooting"
echo ""

echo "Demo complete. Tool is ready for customer service integration!"