// ==========================================
// Povison API Credential Extractor
// Run this in browser console on the quality check page
// ==========================================

console.log("🔍 Starting Povison API Credential Extraction...");

// Method 1: Intercept all network requests
const originalFetch = window.fetch;
const originalXHR = window.XMLHttpRequest;

// Store captured credentials
const capturedCredentials = {
    appId: null,
    appKey: null,
    apiRequests: []
};

// Intercept fetch requests
window.fetch = function(...args) {
    const url = args[0];
    
    if (url.includes('sodaapi.povison-inc.com')) {
        console.log("🎯 API Request detected:", url);
        
        // Try to get headers from the request
        if (args[1] && args[1].headers) {
            const headers = args[1].headers;
            console.log("📋 Request Headers:", headers);
            
            capturedCredentials.apiRequests.push({
                url: url,
                headers: headers,
                method: args[1].method || 'GET',
                timestamp: new Date().toISOString()
            });
        }
    }
    
    return originalFetch.apply(this, args).then(response => {
        if (url.includes('sodaapi.povison-inc.com')) {
            console.log("✅ Response received:", response.status);
        }
        return response;
    });
};

// Intercept XMLHttpRequest
const originalOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url) {
    this._url = url;
    this._method = method;
    return originalOpen.apply(this, arguments);
};

const originalSetHeader = XMLHttpRequest.prototype.setRequestHeader;
XMLHttpRequest.prototype.setRequestHeader = function(header, value) {
    if (this._url && this._url.includes('sodaapi.povison-inc.com')) {
        console.log(`📋 Header: ${header} = ${value}`);
        
        // Capture credential-related headers
        if (header.toLowerCase() === 'appid') {
            capturedCredentials.appId = value;
        } else if (header.toLowerCase() === 'appkey' || header.toLowerCase() === 'x-app-key') {
            capturedCredentials.appKey = value;
        }
    }
    return originalSetHeader.apply(this, arguments);
};

// Method 2: Check localStorage and sessionStorage
console.log("\n🔍 Checking browser storage...");

// Check localStorage
for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key.toLowerCase().includes('app') || key.toLowerCase().includes('auth') || key.toLowerCase().includes('token')) {
        console.log(`🔑 LocalStorage[${key}]:`, localStorage.getItem(key));
        if (key.toLowerCase().includes('id') && !capturedCredentials.appId) {
            capturedCredentials.appId = localStorage.getItem(key);
        }
        if (key.toLowerCase().includes('key') && !capturedCredentials.appKey) {
            capturedCredentials.appKey = localStorage.getItem(key);
        }
    }
}

// Check sessionStorage
for (let i = 0; i < sessionStorage.length; i++) {
    const key = sessionStorage.key(i);
    if (key.toLowerCase().includes('app') || key.toLowerCase().includes('auth') || key.toLowerCase().includes('token')) {
        console.log(`🔑 SessionStorage[${key}]:`, sessionStorage.getItem(key));
        if (key.toLowerCase().includes('id') && !capturedCredentials.appId) {
            capturedCredentials.appId = sessionStorage.getItem(key);
        }
        if (key.toLowerCase().includes('key') && !capturedCredentials.appKey) {
            capturedCredentials.appKey = sessionStorage.getItem(key);
        }
    }
}

// Method 3: Check global variables
console.log("\n🔍 Checking global variables...");
const suspiciousGlobals = [];
for (const key in window) {
    if (key.toLowerCase().includes('app') && typeof window[key] === 'string') {
        suspiciousGlobals.push({ key, value: window[key] });
        console.log(`🌐 window.${key}:`, window[key]);
    }
}

// Method 4: Look for configuration in page scripts
console.log("\n🔍 Analyzing page scripts...");
const scripts = document.querySelectorAll('script[src]');
scripts.forEach(script => {
    if (script.src.includes('config') || script.src.includes('api')) {
        console.log("📜 Config script found:", script.src);
    }
});

// Helper function to display results
function showResults() {
    console.log("\n" + "=".repeat(50));
    console.log("📊 EXTRACTION RESULTS");
    console.log("=".repeat(50));
    
    if (capturedCredentials.appId) {
        console.log("✅ AppId found:", capturedCredentials.appId);
    } else {
        console.log("❌ AppId not found in captured requests");
    }
    
    if (capturedCredentials.appKey) {
        console.log("✅ AppKey found:", capturedCredentials.appKey);
    } else {
        console.log("❌ AppKey not found in captured requests");
    }
    
    console.log(`\n📈 API Requests Captured: ${capturedCredentials.apiRequests.length}`);
    capturedCredentials.apiRequests.forEach((req, index) => {
        console.log(`  ${index + 1}. ${req.method} ${req.url}`);
        console.log(`     Headers:`, JSON.stringify(req.headers, null, 2));
    });
    
    // Generate export commands
    if (capturedCredentials.appId && capturedCredentials.appKey) {
        console.log("\n" + "=".repeat(50));
        console.log("🔧 EXPORT COMMANDS");
        console.log("=".repeat(50));
        console.log(`export POVISON_SODA_API_ID='${capturedCredentials.appId}'`);
        console.log(`export POVISON_SODA_API_KEY='${capturedCredentials.appKey}'`);
        console.log("\nOr add to ~/.hermes/profiles/povison-cs/.env:");
        console.log(`POVISON_SODA_API_ID='${capturedCredentials.appId}'`);
        console.log(`POVISON_SODA_API_KEY='${capturedCredentials.appKey}'`);
    } else {
        console.log("\n⚠️  Not all credentials found. Try performing a query on the page and check the Network tab manually.");
    }
}

// Auto-show results after 10 seconds or call manually
setTimeout(showResults, 10000);

// Also make function available globally
window.showExtractionResults = showResults;
window.capturedCredentials = capturedCredentials;

console.log("\n✅ Network interception active!");
console.log("💡 Now perform a query on the page, then call showExtractionResults() or wait 10 seconds");
console.log("💡 Or check the Network tab in Developer Tools (F12) for sodaapi.povison-inc.com requests");