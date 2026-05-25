# Povison Product Tool

Lookup Povison product metadata from a product page URL. The tool extracts the API `path` and `variant` query parameter, then calls:

`https://www.povison.com/api/product-server/openApi/product/modelProductList`

## Configuration

Set the client token in the environment:

```bash
export POVISON_CLIENT_TOKEN="..."
```

The tool always sends `storeId=3` in both the JSON body and request header, matching the US product page pricing observed during validation.

## Example

```json
{
  "url": "https://www.povison.com/products/tv-stand-43956.html?variant=43962"
}
```

The response includes the parsed request fields, product summary, selected SKU, available option groups, and all variant/SKU mappings.

## Notes

- Tokens are read from the environment and are never logged or echoed by the tool.
- Links must include a `variant` query parameter.
- Pass `include_raw=true` only when the full API payload is needed, because it can be large.