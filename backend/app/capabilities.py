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
    "intake.read",
    "intake.write",
    "intake.review",
    "import.run",
    "excel.use",
    "export.run",
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
        "intake.write",
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
        "intake.read",
        "intake.write",
        "intake.review",
        "import.run",
        "excel.use",
        "export.run",
        *_COMMON,
    ],
    "marketing": ["client.read_min", "excel.use", *_COMMON],
    "manager": list(CAPABILITIES),
}
ROLES = list(ROLE_CAPABILITIES)
