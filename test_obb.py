import os
from pathlib import Path
p = Path('.').absolute()
os.environ['OPENBB_USER_SETTINGS_DIRECTORY'] = str(p/'.openbb_platform')
os.environ['OPENBB_SYSTEM_SETTINGS_DIRECTORY'] = str(p/'.openbb_platform')
os.environ['OPENBB_APPLICATION_DIRECTORY'] = str(p/'.openbb_platform')
os.environ['OPENBB_HOME_DIRECTORY'] = str(p/'.openbb_platform')
try:
    from openbb import obb
    print("Providers:", obb.coverage.providers)
    # Try a quote without specifying provider
    try:
        res = obb.equity.price.quote("AAPL")
        print("Default Quote:", res)
    except Exception as e:
        print("Default Quote Failed:", e)
except Exception as e:
    print("Import failed:", e)
