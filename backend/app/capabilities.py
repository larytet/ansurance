"""Roles and named capabilities (REQUIREMENTS section 2)."""

CAPABILITIES = [
    "client.read",
    "client.read_all",
    "client.read_min",
    "client.write",
    "policy.read",
    "policy.write",
    "audit.read",
    "user.manage",
]

ROLE_CAPABILITIES: dict[str, list[str]] = {
    "agent": [
        "client.read",
        "client.write",
        "policy.read",
        "policy.write",
        "document.read",
        "document.write",
    ],
    "backoffice": ["client.read", "client.read_all", "client.write", "policy.read", "policy.write"],
    "marketing": ["client.read_min"],
    "manager": CAPABILITIES,
}
ROLES = list(ROLE_CAPABILITIES)
