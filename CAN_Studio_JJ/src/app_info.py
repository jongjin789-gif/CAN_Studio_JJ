"""Program name and deployment file names, shared by main.py, the UI and build_exe.py."""
import os
import shutil

APP_NAME = 'CAN Studio JJ'
APP_FILE_PREFIX = 'CAN_Studio_JJ'
VIEWER_APP_NAME = 'CAN Studio JJ Log Viewer'
VIEWER_FILE_PREFIX = 'CAN_Studio_Viewer_JJ'

# Launch flags/TX packet file next to the executable. The name changed in R013;
# the old file is still read so existing deployments keep their settings.
PK_FILE_NAME = APP_FILE_PREFIX + '.pk'
LEGACY_PK_FILE_NAMES = ('PCAN_Viewer_JJ.pk',)


def pk_path(base_dir):
    """Return the .pk path in ``base_dir``, copying a legacy file once if needed.

    The legacy file is left untouched so an older executable in the same folder
    still works. If the copy fails, the legacy file is used directly.
    """
    path = os.path.join(base_dir, PK_FILE_NAME)
    if os.path.exists(path):
        return path
    for name in LEGACY_PK_FILE_NAMES:
        legacy = os.path.join(base_dir, name)
        if os.path.exists(legacy):
            try:
                shutil.copy2(legacy, path)
            except OSError:
                return legacy
            return path
    return path
