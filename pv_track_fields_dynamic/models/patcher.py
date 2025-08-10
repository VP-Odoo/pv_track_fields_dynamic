from odoo import api, fields, models, _
from odoo.models import BaseModel
from odoo.tools import format_datetime
from markupsafe import Markup  # ensure HTML is treated as safe

# ---------------- Common guards ----------------

def _tfd_is_ready(env):
    """Only run tracking when registry is ready and our config model exists,
    and we're NOT in install/upgrade mode."""
    if env.context.get('install_mode') or env.context.get('update_module'):
        return False
    return 'track.fields.config' in env.registry.models

# -------- Helpers (pure functions) --------

def _format_value(env, rec, field, raw_value):
    if raw_value in (False, None):
        return '—'
    ttype = field.type

    if ttype == 'many2one':
        if not raw_value:
            return '—'
        name = env[field.comodel_name].browse(raw_value).display_name
        return name or str(raw_value)

    if ttype in ('many2many', 'one2many'):
        ids = list(raw_value or [])
        if not ids:
            return '—'
        names = env[field.comodel_name].browse(ids).mapped('display_name')
        return ', '.join(names) or '—'

    if ttype == 'datetime':
        return format_datetime(env, raw_value)
    if ttype == 'date':
        return fields.Date.to_string(raw_value)
    if ttype == 'monetary':
        currency_field = getattr(field, 'currency_field', None) or 'currency_id'
        currency = getattr(rec, currency_field, False) or env.company.currency_id
        symbol = getattr(currency, 'symbol', '') or ''
        try:
            return f"{symbol} {float(raw_value):.2f}"
        except Exception:
            return str(raw_value)
    if ttype == 'selection':
        return dict(rec._fields[field.name].selection).get(raw_value, raw_value) if raw_value else '—'
    if ttype == 'boolean':
        return _('Yes') if raw_value else _('No')
    if ttype == 'binary':
        return _('[binary]')
    return str(raw_value)

def _snapshot_for(rec, field_names):
    snap = {}
    for fname in field_names:
        fld = rec._fields.get(fname)
        if not fld:
            continue
        val = rec[fname]
        if fld.type in ('many2many', 'one2many'):
            snap[fname] = set(val.ids)
        elif fld.type == 'many2one':
            snap[fname] = val.id
        else:
            snap[fname] = val
    return snap

def _build_lines(env, rec, cfg, before, after):
    """Build HTML lines describing differences according to cfg,
       using 'old → new' like native tracking."""
    lines = []
    names = {f.name for f in cfg.field_ids}
    for fname in names:
        fld = rec._fields.get(fname)
        if not fld:
            continue

        old_raw = before.get(fname)
        new_raw = after.get(fname)

        # Equality check / normalization
        if fld.type in ('many2many', 'one2many'):
            old_set = old_raw or set()
            new_set = new_raw or set()
            if old_set == new_set:
                continue
            old_disp = _format_value(env, rec, fld, old_set)
            new_disp = _format_value(env, rec, fld, new_set)
        else:
            if old_raw == new_raw:
                continue
            old_disp = _format_value(env, rec, fld, old_raw)
            new_disp = _format_value(env, rec, fld, new_raw)

        if cfg.exclude_empty_changes and (old_disp == new_disp):
            continue

        label = fld.string or fname

        # Decide how to render depending on options
        if cfg.show_old_values and cfg.show_new_values:
            value_html = f"<span>{old_disp}</span>&nbsp;→&nbsp;<span>{new_disp}</span>"
        elif cfg.show_new_values and not cfg.show_old_values:
            value_html = f"<span>{new_disp}</span>"
        elif cfg.show_old_values and not cfg.show_new_values:
            value_html = f"<span>{old_disp}</span>"
        else:
            # If both options are off, nothing to show for this field
            continue

        lines.append(f"• <b>{label}</b> — {value_html}")
    return lines

# -------- Cached config lookup --------

def _get_cfg(env, model_name):
    if not _tfd_is_ready(env):
        return False
    ctx = dict(env.context)
    cache = ctx.get('_tfd_cfg_cache', {})
    key = (env.company.id, model_name)
    if key not in cache:
        cfg = env['track.fields.config'].sudo().search([
            ('active', '=', True),
            ('company_id', '=', env.company.id),
            ('model_id.model', '=', model_name),
        ], limit=1)
        cache[key] = cfg
        env.context = dict(env.context, _tfd_cfg_cache=cache)
    return cache[key]

# -------- Global patch installer --------

class TrackFieldsGlobalPatcher(models.AbstractModel):
    _name = 'track.fields.global.patcher'
    _description = 'Installs cross-model write/create hooks for dynamic field tracking'

    def _register_hook(self):
        super()._register_hook()

        # --- Patch write ---
        if not getattr(BaseModel, '_tfd_orig_write', None):
            BaseModel._tfd_orig_write = BaseModel.write

            def tfd_write(self, vals):
                if not self or not _tfd_is_ready(self.env):
                    return BaseModel._tfd_orig_write(self, vals)

                env = self.env
                cfg = _get_cfg(env, self._name)
                if not cfg or not getattr(cfg, 'field_ids', False):
                    return BaseModel._tfd_orig_write(self, vals)

                can_chatter = hasattr(self, 'message_post') and 'message_ids' in self._fields
                if not can_chatter:
                    return BaseModel._tfd_orig_write(self, vals)

                tracked_fields = [f.name for f in cfg.field_ids if f.name in self._fields]
                if not tracked_fields:
                    return BaseModel._tfd_orig_write(self, vals)

                before_map = {rec.id: _snapshot_for(rec, tracked_fields) for rec in self}
                res = BaseModel._tfd_orig_write(self, vals)
                if not res:
                    return res

                changes_by_rec = {}
                for rec in self:
                    after = _snapshot_for(rec, tracked_fields)
                    lines = _build_lines(env, rec, cfg, before_map.get(rec.id, {}), after)
                    if not lines:
                        continue
                    if cfg.group_changes_per_record:
                        changes_by_rec.setdefault(rec, []).extend(lines)
                    else:
                        for line in lines:
                            rec.message_post(
                                body=Markup(f"<div>{line}</div>"),
                                message_type='comment',
                                subtype_xmlid='mail.mt_note',
                            )
                if cfg.group_changes_per_record and changes_by_rec:
                    for rec, lines in changes_by_rec.items():
                        rec.message_post(
                            body=Markup("<div>" + "<br/>".join(lines) + "</div>"),
                            message_type='comment',
                            subtype_xmlid='mail.mt_note',
                        )

                return res

            BaseModel.write = tfd_write

        # --- Patch create ---
        if not getattr(BaseModel, '_tfd_orig_create', None):
            BaseModel._tfd_orig_create = BaseModel.create

            @api.model_create_multi
            def tfd_create(self, vals_list):
                recs = BaseModel._tfd_orig_create(self, vals_list)
                if not recs or not _tfd_is_ready(recs.env):
                    return recs

                env = recs.env
                cfg = _get_cfg(env, recs._name)
                if not cfg or not getattr(cfg, 'field_ids', False):
                    return recs

                can_chatter = hasattr(recs, 'message_post') and 'message_ids' in recs._fields
                if not can_chatter:
                    return recs

                tracked_fields = [f.name for f in cfg.field_ids if f.name in recs._fields]
                if not tracked_fields:
                    return recs

                changes_by_rec = {}
                for rec, vals in zip(recs, vals_list):
                    consider = [f for f in tracked_fields if f in vals]
                    if not consider:
                        continue
                    after = _snapshot_for(rec, consider)
                    before = {f: False for f in consider}
                    lines = _build_lines(env, rec, cfg, before, after)
                    if lines:
                        if cfg.group_changes_per_record:
                            changes_by_rec.setdefault(rec, []).extend(lines)
                        else:
                            for line in lines:
                                rec.message_post(
                                    body=Markup(f"<div>{line}</div>"),
                                    message_type='comment',
                                    subtype_xmlid='mail.mt_note',
                                )

                if cfg.group_changes_per_record and changes_by_rec:
                    for rec, lines in changes_by_rec.items():
                        rec.message_post(
                            body=Markup("<div>" + "<br/>".join(lines) + "</div>"),
                            message_type='comment',
                            subtype_xmlid='mail.mt_note',
                        )

                return recs

            BaseModel.create = tfd_create
