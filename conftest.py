"""Makes `import src...` work when running pytest or the app from the project
root, without needing an editable install.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
