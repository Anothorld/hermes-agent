# CS Bridge Agent Guard

Blocks `quickcep send-email` in `povison-cs:*` gateway runs so automation only writes QuickCEP drafts.

## Enable

Auto-enabled when the plugin is loaded. Disable with:

```bash
export CS_BRIDGE_AGENT_GUARD=0
```

## What is blocked

Terminal commands matching QuickCEP operator send-email paths (`quickcep_cli.py send-email`, `/im/message/operator/sendEmail`).

## What is allowed

- `draft-save`, `draft-get`, bridge CLI, Feishu escalation scripts
- All tools on non-`povison-cs:` sessions
