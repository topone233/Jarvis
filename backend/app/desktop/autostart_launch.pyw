# 注册表自启动项指向的入口：pythonw 直接执行（无控制台窗口），任意 cwd 都能起。
# 真正的实现在 app/desktop/__main__.py，这里只负责把 backend 挂上 sys.path。
import os
import sys
from pathlib import Path

backend = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(backend))
os.chdir(backend)

from app.desktop.__main__ import main  # noqa: E402

main(hidden="--hidden" in sys.argv)
