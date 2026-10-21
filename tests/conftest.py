import sys
from pathlib import Path

# 让测试能 import 上一层的 MediaGrab.py
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
