#!/bin/bash
# Quick start script for API credential extraction

echo "🔍 Povison API Credential Extraction Quick Start"
echo "================================================"
echo ""

# Check if we have the extraction script
EXTRACTOR_SCRIPT="/Users/arnold/agent_prj/hermes-agent/scripts/browser-auth-extractor.js"
GUIDE_FILE="/Users/arnold/agent_prj/hermes-agent/API_CREDENTIAL_GUIDE.md"

if [ -f "$EXTRACTOR_SCRIPT" ]; then
    echo "✅ Browser extractor script found"
    echo "📂 Location: $EXTRACTOR_SCRIPT"
else
    echo "❌ Browser extractor script not found"
fi

if [ -f "$GUIDE_FILE" ]; then
    echo "✅ Complete guide found"
    echo "📂 Location: $GUIDE_FILE"
else
    echo "❌ Complete guide not found"
fi

echo ""
echo "🚀 Quick Steps:"
echo ""
echo "1. 🌐 Open your Povison quality check webpage"
echo "2. 🔧 Press F12 to open Developer Tools"
echo "3. 💻 Go to Console tab"
echo "4. 📋 Copy and paste the JavaScript code from:"
echo "   $EXTRACTOR_SCRIPT"
echo "5. ▶️  Press Enter to execute the script"
echo "6. 🔍 Perform a query on the webpage"
echo "7. 📊 Check the console for extracted credentials"
echo ""
echo "📖 For detailed instructions, see:"
echo "   $GUIDE_FILE"
echo ""

echo "🔧 Manual Extraction Method (Alternative):"
echo ""
echo "1. 🌐 Open the webpage and press F12"
echo "2. 📊 Go to Network tab"
echo "3. 🧹 Clear existing requests"
echo "4. 🔍 Perform a query on the page"
echo "5. 🔎 Look for requests to: sodaapi.povison-inc.com"
echo "6. 📋 Click the request and check Headers"
echo "7. 🔑 Look for 'appId' and 'appKey' in Request Headers"
echo ""

echo "📝 After extraction, add credentials to:"
echo "   ~/.hermes/profiles/povison-cs/.env"
echo ""
echo "   export POVISON_SODA_API_ID='your-found-app-id'"
echo "   export POVISON_SODA_API_KEY='your-found-app-key'"
echo ""

echo "🧪 Test the configuration:"
echo "   ./scripts/qc-images query --psku TEST-SKU --size 1"
echo ""

echo "🆘 If you need help:"
echo "   - Read the complete guide: $GUIDE_FILE"
echo "   - Contact IT department for official API access"
echo "   - Check the browser console for error messages"
echo ""

# Optional: Copy the JavaScript script to clipboard for easy pasting
if command -v pbcopy >/dev/null 2>&1; then
    echo "📋 JavaScript script ready to copy to clipboard..."
    read -p "Press Enter to copy the extraction script to clipboard: "
    cat "$EXTRACTOR_SCRIPT" | pbcopy
    echo "✅ Script copied to clipboard! Paste it in browser console."
else
    echo "💡 Tip: Copy the content of $EXTRACTOR_SCRIPT to your clipboard"
    echo "   then paste it in the browser console."
fi

echo ""
echo "Good luck with the extraction! 🚀"