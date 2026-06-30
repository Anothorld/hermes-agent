# Povison Quality Check Images API Integration

## Overview
This integration enables customer service agents to query and present product quality check images to customers, and attach them to QuickCEP email drafts.

## Components

### 1. Core API Client (`scripts/quality_check_images.py`)
- Python implementation of the Quality Check Images API
- HMAC-SHA256 authentication
- Supports filtering by SKU, QC code, date range
- Formatted output for customer presentation

### 2. CLI Wrapper (`scripts/qc-images`)
- Easy-to-use command-line interface
- Auto-installs dependencies
- Simplifies syntax for daily use

### 3. Demo & Documentation (`scripts/qc-images-demo.sh`)
- Usage examples and workflows
- Setup instructions
- Integration guidelines

### 4. Skill Documentation (`skills/povison-quality-check-images/SKILL.md`)
- Agent skill for automated usage
- Customer service workflows
- Error handling guidelines

## Installation

### Prerequisites
```bash
# Set environment variables in ~/.hermes/profiles/povison-cs/.env
export POVISON_SODA_API_ID='your-app-id'
export POVISON_SODA_API_KEY='your-app-key'
```

### Dependencies
- Python 3.x
- requests library (auto-installed by wrapper)

## Usage

### Basic Query
```bash
./scripts/qc-images query --psku P-SKU-001
```

### With Options
```bash
./scripts/qc-images query --psku P-SKU-001 --version V1 --size 5 --page 1
```

### Get Image URLs Only
```bash
./scripts/qc-images urls --psku P-SKU-001
```

### Advanced Filtering
```bash
# Filter by QC code
./scripts/qc-images query --psku P-SKU-001 --qc-code QC-20241101-001

# Filter by date range
./scripts/qc-images query --psku P-SKU-001 --date-start 2024-01-01 --date-end 2024-12-31

# Combined filters
./scripts/qc-images query --psku P-SKU-001 --version V1 --date-start 2024-06-01
```

## Customer Service Workflow

### Scenario: Customer Requests Product Images

**Customer:** "Can you show me actual photos of the P-SKU-001 sofa?"

**Agent Response:**
```bash
# 1. Query available images
./scripts/qc-images query --psku P-SKU-001 --size 5

# 2. Present results to customer
# (Output shows formatted summary with image URLs)

# 3. If customer wants images in email
# Add to QuickCEP draft with attachment URLs
# (Integration with existing QuickCEP tools)
```

### Response Format
```
Found 3 quality check image(s):

1. SKU: P-SKU-001 | QC Code: QC-20241101-001 | Date: 2024-11-01
   Image URL: https://oss.example.com/quality/check/2024/11/QC-20241101-001_img1.jpg

2. SKU: P-SKU-001 | QC Code: QC-20241102-002 | Date: 2024-11-02
   Image URL: https://oss.example.com/quality/check/2024/11/QC-20241102-002_img1.jpg
```

## API Details

### Endpoint
- **URL:** `http://sodaapi.povison-inc.com/api/scm/qualityCheck/imgPage`
- **Method:** POST
- **Auth:** HMAC-SHA256

### Request Parameters
- `pageNo`: Page number (default: 1)
- `pageSize`: Results per page, max 50 (default: 10)
- `psku`: Platform SKU (optional)
- `pskuVersion`: Platform SKU version (optional)
- `qcCode`: Quality check code (optional)
- `dateStart`: Start date yyyy-MM-dd (optional)
- `dateEnd`: End date yyyy-MM-dd (optional)

### Response Fields
- `id`: Record ID
- `psku`: Platform SKU
- `pskuVersion`: Platform SKU version
- `qcCode`: Quality check code
- `actualDate`: Quality check date
- `qualifiedImg`: OSS relative path
- `qualifiedImgUrl`: Full OSS URL
- `createTime`: Record creation time

## QuickCEP Integration

### Adding Images to Drafts
```bash
# Get image URLs
./scripts/qc-images urls --psku P-SKU-001

# Add to QuickCEP draft (example command structure)
# quickcep_cli.py draft-save --session <session_id> --message "Here are the requested images" --attachments <url1>,<url2>
```

### Customer Email Template
```
Subject: Quality Check Images for Your Product (P-SKU-001)

Dear Customer,

Thank you for your interest in our product. Attached are the quality check images 
showing the actual product condition and details.

[Images would be embedded here]

If you have any questions about the product condition or need additional photos, 
please let me know.

Best regards,
Povison Customer Service
```

## Error Handling

### Common Issues
1. **Authentication Failed**
   - Check `POVISON_SODA_API_ID` and `POVISON_SODA_API_KEY` are set
   - Verify credentials are correct and active

2. **No Images Found**
   - Inform customer that no quality check images are available
   - Suggest contacting product team for additional photos

3. **Network Errors**
   - Check internet connectivity
   - Verify API endpoint is accessible
   - Retry once, then escalate if needed

## Testing

### Without Credentials (Test Command Structure)
```bash
./scripts/qc-images query --psku TEST-SKU --size 1
```

### Demo
```bash
./scripts/qc-images-demo.sh
```

## Security Considerations

- API credentials stored in environment variables
- HMAC-SHA256 signature prevents unauthorized access
- Image URLs are public but served from trusted OSS
- No sensitive customer data in API requests

## Maintenance

### Updating Credentials
```bash
# Update environment variables
export POVISON_SODA_API_ID='new-app-id'
export POVISON_SODA_API_KEY='new-app-key'

# Add to .env for persistence
echo "POVISON_SODA_API_ID='new-app-id'" >> ~/.hermes/profiles/povison-cs/.env
echo "POVISON_SODA_API_KEY='new-app-key'" >> ~/.hermes/profiles/povison-cs/.env
```

### Monitoring
- Check API response times
- Monitor authentication failures
- Track image availability per SKU

## Future Enhancements

- [ ] Add image preview/thumbnail generation
- [ ] Implement image caching for performance
- [ ] Add batch SKU queries
- [ ] Integrate with product catalog API
- [ ] Add image quality/size filters
- [ ] Implement image annotation features

## Support

For issues or questions:
1. Check this documentation
2. Review the skill file: `skills/povison-quality-check-images/SKILL.md`
3. Run demo: `./scripts/qc-images-demo.sh`
4. Contact technical support for API credential issues

## Version History

- **v1.0** (2025-06-17): Initial release
  - Basic query functionality
  - CLI wrapper
  - Documentation and demo
  - QuickCEP integration guidelines