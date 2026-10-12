import sys
import os

bot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parent_dir = os.path.dirname(bot_dir)

if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if bot_dir not in sys.path:
    sys.path.insert(0, bot_dir)
