def uninstall_hook(cr, registry):
    # Best-effort cleanup (not strictly necessary because of ondelete='cascade' on ir.model)
    cr.execute("DELETE FROM track_fields_config")
