"""Bindings attached to one TX control, including the legacy primary binding."""

MULTI_TYPES = ('slider', 'button', 'toggle')


def command_bindings(cfg, enabled_only=False):
    primary = cfg.get('binding', {})
    if cfg.get('behavior') != 'tx' or cfg.get('widget_type') not in MULTI_TYPES:
        return [primary]
    commands = [(cfg.get('primary_enabled', True), primary)]
    commands.extend((c.get('enabled', True), c['binding'])
                    for c in cfg.get('tx_commands', []))
    return [binding for enabled, binding in commands if enabled or not enabled_only]
