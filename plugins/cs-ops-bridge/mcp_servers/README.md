# cs-hindsight-knowledge MCP server

FastMCP server exposing the povison-cs dedicated Hindsight knowledge tools.

## Tools

- `knowledge_retain` — refine (de-PII + dual-domain structured metadata + reusable check) then HTTP retain to the Knowledge bank.
- `knowledge_recall` — Intent+Attribute Parser → HTTP recall → credibility/time rerank.
- `knowledge_bank` — returns the hardcoded Knowledge bank id + base URL (bank isolation helper).

## Bank isolation (hard constraint)

Both `knowledge_retain` and `knowledge_recall` target the Knowledge bank (`CS_OPS_HINDSIGHT_KNOWLEDGE_BANK`, default `furniture-knowledge`) and never accept a `bank` override. The Experience bank (`povison-cs-hermes-user`) is legacy read-only and is never used as a business-knowledge source.

## Run / validate

```bash
# from the plugin root
python3 mcp_servers/cs_hindsight_knowledge.py            # run stdio server
fastmcp inspect mcp_servers/cs_hindsight_knowledge.py:mcp
fastmcp list mcp_servers/cs_hindsight_knowledge.py --json
fastmcp call mcp_servers/cs_hindsight_knowledge.py knowledge_bank --json
```

## Env

See `references/hindsight-knowledge-mcp-tools.md` (profile skills) for the full env table. Key vars: `HINDSIGHT_BASE_URL`, `CS_OPS_HINDSIGHT_KNOWLEDGE_BANK`, `CS_HINDSIGHT_LLM_API_KEY` (optional; rule fallback when absent).

## Dependencies

- Prefer `mcp.server.fastmcp` (already in hermes-agent image / `mcp` extra); standalone `fastmcp` 3.x is optional for local dev
- `httpx`, `pydantic` 2.x
- Core logic lives in `../hindsight_ko.py` (self-contained, no Hermes core imports).

## Notes

- The deployed Hindsight 0.8.4 recall endpoint has an upstream `metadata_filter` NameError bug (returns 500 even without a filter). `knowledge_recall` degrades per R7 (retries without the filter) and surfaces the upstream error; retain is unaffected. Track the fix under P1.
