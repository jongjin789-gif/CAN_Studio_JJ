from src.bus_config import BUS_IDS
"""Compare database contents and precedence per BUS, independently of file paths."""
import hashlib
from pathlib import Path
from PyQt5.QtCore import Qt


def database_token(name, raw):
    return Path(name).suffix.lower() + ':' + hashlib.sha256(bytes(raw)).hexdigest()


def database_signature(main):
    listings = getattr(main, 'list_db_files', None)
    if listings is None:
        return getattr(main, 'database_content_signature', None)
    result = {}
    for bus in BUS_IDS:
        result[str(bus)] = []
        listing = listings.get(bus)
        if listing is None:
            continue
        for index in range(listing.count()):
            item = listing.item(index)
            raw = item.data(Qt.UserRole + 2)
            if raw is None:
                return None
            result[str(bus)].append(database_token(item.text(), raw))
    return result
