import sys
from pathlib import Path

# 在仓库根也能跑：server/（account、store）与仓库根（listen 包）都进 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
