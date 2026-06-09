"""OpenAI function schema for mysql_execute_sql."""

MYSQL_EXECUTE_SQL_SCHEMA = {
    "name": "mysql_execute_sql",
    "description": (
        "Execute a read-only MySQL query (SELECT / SHOW / EXPLAIN / DESCRIBE). "
        "This is the ONLY supported way for the agent to run SQL against the "
        "data-analyst database. Every call requires Hermes clarify human approval "
        "unless the operator already chose session-wide skip for this conversation. "
        "Do not use terminal, execute_code, mysql CLI, or sql_executor.py."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "The SQL statement to execute (SELECT / SHOW / EXPLAIN / DESCRIBE only).",
            },
            "database": {
                "type": "string",
                "description": "Optional database name override. Defaults to mysql_tools.yaml target_database.",
            },
        },
        "required": ["sql"],
    },
}
