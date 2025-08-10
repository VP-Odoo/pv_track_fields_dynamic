{
    "name": "Dynamic Field Tracking",
    "summary": "Configure which fields to track; applies across all models without per-model inheritance.",
    "version": "18.0.1.0.0",
    "author": "PV-Odoo",
    "license": "LGPL-3",
    "category": "Tools",
    "depends": ["base", "mail"],
    "images": ["static/description/banner.png"],
    "data": [
        # Load views that DEFINE the action first:
        "views/track_config_views.xml",
        # Then load menus that REFERENCE that action:
        "views/menu.xml",
        "security/ir.model.access.csv"
    ],
    "installable": True,
    "application": False,
    "uninstall_hook": "uninstall_hook",
    "description": """
Dynamic Field Tracking — Odoo 18

Choose exactly which fields on any model should be tracked. The module posts clean,
native-style “old → new” entries in the chatter — without touching model code or
adding bridge modules.

Key Features
• Track any model/field available in the DB (stored, non-binary).
• Per-company configuration at Settings → Administration → Dynamic Tracking.
• “Old → New” formatting, grouped into a single note per record change.
• Smart rendering for many2one / many2many / one2many / date / datetime / monetary /
  selection / boolean. (Binary excluded by design.)
• Safe & upgrade-friendly: activates only on models with mail.thread; skips during
  install/upgrade; minimal in-request caching.
• Clean uninstall: configs link to ir.model with ondelete=cascade.

How It Works
• Lightweight global hook wraps ORM create/write.
• Takes before/after snapshots only for selected fields.
• Posts as the current user using subtype mail.mt_note.

Usage
1) Install the module.
2) Open Settings → Administration → Dynamic Tracking → Configurations.
3) Pick Company, Model and Fields; adjust options (grouping, show old/new).
4) Change a tracked field on a record with chatter — see “old → new” entries.

Notes / Limits
• Models without chatter are skipped (no errors).
• Many2many/one2many display the full new set; delta (+Added/–Removed) is optional.
""",
}
