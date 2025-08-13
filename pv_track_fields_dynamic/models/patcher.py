# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.models import BaseModel
from odoo.tools import format_datetime
from markupsafe import Markup
import logging

_logger = logging.getLogger(__name__)

# -- Technical models we must never touch with business tracking --
TECH_MODELS = {
    "ir.module.module",
    "base.module.uninstall",
    "ir.ui.view",
    "ir.model",
    "ir.model.fields",
    "ir.actions.act_window",
    "ir.actions.server",
    "ir.cron",
    "ir.rule",
    "ir.config_parameter",
    "bus.bus",
}

# ---------------- Guards ----------------

def _tfd_is_ready(env):
    """
    Run tracking only when:
      - uninstall is NOT in progress
      - the config model exists in the registry
    NOTE: we do NOT blanket-skip on 'update_module' because that flag can appear
    in normal UI contexts. We specifically skip when 'module_uninstall' is set.
    """
    ctx = env.context or {}
    if ctx.get("module_uninstall") or ctx.get("install_mode"):
        return False
    return "track.fields.config" in getattr(env.registry, "models", {})

# -------- Helpers --------

def _format_value(env, rec, field, raw_value):
    """Pretty print values based on ir.model.fields.ttype."""
    if raw_value is None:
        return _("False")
    t = getattr(field, "ttype", "") or ""
    if t in ("char", "text", "html"):
        return str(raw_value)
    if t == "boolean":
        return _("True") if raw_value else _("False")
    if t in ("integer", "float", "monetary"):
        return str(raw_value)
    if t == "date":
        try:
            return fields.Date.to_string(raw_value)
        except Exception:
            return str(raw_value)
    if t == "datetime":
        try:
            return format_datetime(env, raw_value, tz=env.user.tz)
        except Exception:
            return str(raw_value)
    if t == "many2one":
        try:
            return raw_value.display_name if hasattr(raw_value, "display_name") else str(raw_value)
        except Exception:
            return str(raw_value)
    if t in ("many2many", "one2many"):
        try:
            return ", ".join(raw_value.mapped("display_name")) if hasattr(raw_value, "mapped") else str(raw_value)
        except Exception:
            return str(raw_value)
    return str(raw_value)

def _snapshot_for(rec, field_names):
    return {fname: rec[fname] for fname in field_names if fname in rec._fields}

def _build_lines(env, rec, cfg, before, after):
    lines = []
    for f in cfg.field_ids:  # f is ir.model.fields
        fname = f.name
        if fname not in before or fname not in after:
            continue
        old = before[fname]
        new = after[fname]
        # Normalize for comparison
        if f.ttype == "many2one":
            old_cmp = getattr(old, "id", old)
            new_cmp = getattr(new, "id", new)
        elif f.ttype in ("many2many", "one2many"):
            old_cmp = tuple(sorted(getattr(old, "ids", []) or []))
            new_cmp = tuple(sorted(getattr(new, "ids", []) or []))
        else:
            old_cmp = old
            new_cmp = new
        if old_cmp == new_cmp:
            continue
        lines.append(
            _("%(field)s: %(old)s → %(new)s")
            % {
                "field": f.field_description or f.name,
                "old": _format_value(env, rec, f, old) if getattr(cfg, "show_old_values", True) else "",
                "new": _format_value(env, rec, f, new) if getattr(cfg, "show_new_values", True) else "",
            }
        )
    return lines

# -------- Config fetch (hardened + cached) --------

def _get_cfg(env, model_name):
    """Return active config for (company, model_name) or False. Never raise."""
    if not _tfd_is_ready(env):
        return False
    try:
        cache = dict(env.context.get("_tfd_cfg_cache", {}))
        key = (env.company.id, model_name)
        if key not in cache:
            cfg = env["track.fields.config"].sudo().search(
                [
                    ("active", "=", True),
                    ("company_id", "=", env.company.id),
                    ("model_id.model", "=", model_name),
                ],
                limit=1,
            )
            cache[key] = cfg
            env.context = dict(env.context, _tfd_cfg_cache=cache)
        return cache[key]
    except Exception as e:
        _logger.debug("TFD: _get_cfg failed for %s: %s", model_name, e)
        return False

# -------- Global patch installer --------

class TrackFieldsGlobalPatcher(models.AbstractModel):
    _name = "track.fields.global.patcher"
    _description = "Installs cross-model write/create hooks for dynamic field tracking"

    def _register_hook(self):
        super()._register_hook()

        # --- Patch write ---
        if not getattr(BaseModel, "_tfd_orig_write", None):
            BaseModel._tfd_orig_write = BaseModel.write

            def tfd_write(self, vals):
                # Hard bail-outs for technical models and uninstall
                if self._name in TECH_MODELS:
                    return BaseModel._tfd_orig_write(self, vals)
                if not self or not _tfd_is_ready(self.env):
                    return BaseModel._tfd_orig_write(self, vals)

                env = self.env
                cfg = _get_cfg(env, self._name)
                if not cfg or not getattr(cfg, "field_ids", False):
                    return BaseModel._tfd_orig_write(self, vals)

                if not (hasattr(self, "message_post") and "message_ids" in self._fields):
                    return BaseModel._tfd_orig_write(self, vals)

                tracked_names = [f.name for f in cfg.field_ids if f.name in self._fields]
                if not tracked_names:
                    return BaseModel._tfd_orig_write(self, vals)

                before_by_id = {rec.id: _snapshot_for(rec, tracked_names) for rec in self}
                res = BaseModel._tfd_orig_write(self, vals)
                after_by_id = {rec.id: _snapshot_for(rec, tracked_names) for rec in self}

                if cfg.group_changes_per_record:
                    for rec in self:
                        lines = _build_lines(env, rec, cfg, before_by_id.get(rec.id, {}), after_by_id.get(rec.id, {}))
                        if lines:
                            rec.message_post(
                                body=Markup("<div>" + "<br/>".join(lines) + "</div>"),
                                message_type="comment",
                                subtype_xmlid="mail.mt_note",
                            )
                else:
                    blocks = []
                    for rec in self:
                        lines = _build_lines(env, rec, cfg, before_by_id.get(rec.id, {}), after_by_id.get(rec.id, {}))
                        if lines:
                            blocks.append("<div><b>%s</b><br/>%s</div>" % (rec.display_name, "<br/>".join(lines)))
                    if blocks:
                        self[0].message_post(
                            body=Markup("<div>" + "".join(blocks) + "</div>"),
                            message_type="comment",
                            subtype_xmlid="mail.mt_note",
                        )

                return res

            BaseModel.write = tfd_write

        # --- Patch create ---
        if not getattr(BaseModel, "_tfd_orig_create", None):
            BaseModel._tfd_orig_create = BaseModel.create

            @api.model_create_multi
            def tfd_create(self, vals_list):
                # Hard bail-outs for technical models and uninstall
                if self._name in TECH_MODELS:
                    return BaseModel._tfd_orig_create(self, vals_list)
                env = self.env
                if not _tfd_is_ready(env):
                    return BaseModel._tfd_orig_create(self, vals_list)

                cfg = _get_cfg(env, self._name)
                if not cfg or not getattr(cfg, "field_ids", False):
                    return BaseModel._tfd_orig_create(self, vals_list)

                if not (hasattr(self, "message_post") and "message_ids" in self._fields):
                    return BaseModel._tfd_orig_create(self, vals_list)

                tracked_names = [f.name for f in cfg.field_ids if f.name in self._fields]
                if not tracked_names:
                    return BaseModel._tfd_orig_create(self, vals_list)

                recs = BaseModel._tfd_orig_create(self, vals_list)

                if getattr(cfg, "track_on_create", False):
                    if cfg.group_changes_per_record:
                        for rec in recs:
                            after = _snapshot_for(rec, tracked_names)
                            lines = []
                            for f in cfg.field_ids:
                                fname = f.name
                                if fname in after:
                                    lines.append(
                                        _("%(field)s: %(new)s")
                                        % {
                                            "field": f.field_description or f.name,
                                            "new": _format_value(env, rec, f, after[fname]),
                                        }
                                    )
                            if lines:
                                rec.message_post(
                                    body=Markup("<div>" + "<br/>".join(lines) + "</div>"),
                                    message_type="comment",
                                    subtype_xmlid="mail.mt_note",
                                )
                    else:
                        blocks = []
                        for rec in recs:
                            after = _snapshot_for(rec, tracked_names)
                            lines = []
                            for f in cfg.field_ids:
                                fname = f.name
                                if fname in after:
                                    lines.append(
                                        _("%(field)s: %(new)s")
                                        % {
                                            "field": f.field_description or f.name,
                                            "new": _format_value(env, rec, f, after[fname]),
                                        }
                                    )
                            if lines:
                                blocks.append("<div><b>%s</b><br/>%s</div>" % (rec.display_name, "<br/>".join(lines)))
                        if blocks:
                            recs[0].message_post(
                                body=Markup("<div>" + "".join(blocks) + "</div>"),
                                message_type="comment",
                                subtype_xmlid="mail.mt_note",
                            )

                return recs

            BaseModel.create = tfd_create
