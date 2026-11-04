"""Preserve FD metadata that database parsers may omit."""
import re
import cantools


def compatible_signal_mapping(source, target, signal_names=None):
    """Allow a bus-only change when the saved signal interpretation still fits."""
    if source is None or target is None or source.frame_id != target.frame_id or source.length != target.length:
        return False
    names = set(signal_names) if signal_names is not None else {s.name for s in source.signals}
    fields = ('start', 'length', 'byte_order', 'is_signed', 'scale', 'offset',
              'is_multiplexer', 'multiplexer_ids', 'multiplexer_signal')
    old = {s.name: s for s in source.signals}
    new = {s.name: s for s in target.signals}
    return all(name in old and name in new and
               all(getattr(old[name], key, None) == getattr(new[name], key, None) for key in fields)
               for name in names)


def is_fd_format(value):
    normalized = re.sub(r'[^a-z0-9]', '', str(value).lower())
    return normalized in {'fd', 'fdstandard', 'fdextended', 'fdcan', 'canfd',
                          'standardcanfd', 'extendedcanfd', 'standardfd', 'extendedfd'}


def message_is_fd(message):
    if message.is_fd or message.length > 8:
        return True
    dbc = getattr(message, 'dbc', None)
    attributes = getattr(dbc, 'attributes', {})
    for key in ('VFrameFormat', 'BusType', 'Type'):
        attribute = attributes.get(key)
        if attribute is None:
            continue
        value = attribute.value
        choices = getattr(getattr(attribute, 'definition', None), 'choices', None)
        if isinstance(value, int) and choices and 0 <= value < len(choices):
            value = choices[value]
        if is_fd_format(value):
            return True
    return False


def load_sym_with_fd(content):
    fd_names = set()
    name = None
    lines = []
    in_enum = False
    for line in content.splitlines():
        if re.match(r'^\s*Enum\s*=', line):
            in_enum = True
        if in_enum:
            # cantools rejects -b after enums. Find the closing parenthesis
            # outside labels/comments, including on multiline definitions.
            for token in re.finditer(r'"(?:\\.|[^"\\])*"|//.*|[()]', line):
                if token.group() == ')':
                    end = token.end()
                    line = line[:end] + re.sub(r'^([ \t]*)-b(?=[ \t]*(?://|$))',
                                              r'\1', line[end:])
                    in_enum = False
                    break
        if re.match(r'^\s*(?:Var|Sig)\s*=', line):
            # Some SYM exporters use /O: for offset; cantools expects /o:.
            # Preserve quoted labels and comments verbatim.
            line = re.sub(r'("(?:\\.|[^"\\])*"|//.*)|(?<!\S)/O:',
                          lambda match: match.group(1) if match.group(1) is not None else '/o:', line)
        if re.match(r'^\s*FormatVersion\s*=', line) and '//' not in line:
            line += ' // Symbol file'
        section = re.match(r'^\s*\[([^\]]+)\]', line)
        if section:
            name = section.group(1)
        match = re.match(r'^\s*Type\s*=\s*(.*?)\s*(?://.*)?$', line, re.IGNORECASE)
        if match and is_fd_format(match.group(1)):
            fd_names.add(name)
            # Normalize aliases, including the two-word "FD CAN", for cantools.
            line = 'Type=FDExtended' if 'extended' in match.group(1).lower() else 'Type=FDStandard'
        lines.append(line)
    database = cantools.database.load_string('\n'.join(lines), database_format='sym', strict=False)
    for message in database.messages:
        if message.name in fd_names:
            message.is_fd = True
    return database
