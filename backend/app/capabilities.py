"""Roles and named capabilities (REQUIREMENTS section 2).

This module is the source of truth for the *current* roles and capabilities. The
database holds the seeded copy (see the migrations); tests/test_capabilities.py fails
when the two differ, so a change here needs a migration.
"""

CAPABILITIES = [
    "client.read",
    "client.read_all",
    "client.read_min",
    "client.write",
    "policy.read",
    "policy.write",
    "audit.read",
    "user.manage",
    "document.read",
    "document.write",
    "document.hold",
    "inbox.read",
    "inbox.create",
    "inbox.view_all",
    "consent.read",
    "consent.write",
    "message.send",
    "desk.read",
    "process.read",
    "process.start",
    "process.define",
    "process.cancel",
]

_COMMON = [
    "inbox.read",
    "inbox.create",
    "consent.read",
    "consent.write",
    "message.send",
    "process.read",
    "process.start",
]

ROLE_CAPABILITIES: dict[str, list[str]] = {
    "agent": [
        "client.read",
        "client.write",
        "policy.read",
        "policy.write",
        "document.read",
        "document.write",
        *_COMMON,
    ],
    "backoffice": [
        "client.read",
        "client.read_all",
        "client.write",
        "policy.read",
        "policy.write",
        "document.read",
        "document.write",
        "desk.read",
        *_COMMON,
    ],
    "marketing": ["client.read_min", *_COMMON],
    "manager": list(CAPABILITIES),
}
ROLES = list(ROLE_CAPABILITIES)
