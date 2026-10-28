from vitalocal.config import get_settings
from vitalocal.rawstore import RawStore


def get_store() -> RawStore:
    return RawStore(get_settings().raw_store_path)
