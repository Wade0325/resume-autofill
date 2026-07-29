"""打包版的後端進入點：由 ResumeAutoFill.exe（C# 啟動器）以內嵌 Python 執行。

開發時直接 `python run_backend.py` 也可以，效果等同
`python -m uvicorn backend.main:app`（但不含 --reload）。
"""
import uvicorn

from backend import config
from backend.main import app

if __name__ == "__main__":
    # log_config=None：uvicorn 不要接管 logging，全部交給 backend.logging_setup
    uvicorn.run(app, host=config.API_HOST, port=config.API_PORT, log_config=None)
