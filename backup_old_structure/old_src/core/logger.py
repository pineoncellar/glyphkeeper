"""
日志模块
"""

import sys
import logging
import yaml
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

def _load_debug_mode() -> bool:
    """从 config.yaml 读取 debug 配置"""
    try:
        config_path = PROJECT_ROOT / "config.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                if config and "project" in config:
                    return config["project"].get("debug", False)
    except Exception as e:
        # 在日志系统初始化前，只能打印到标准错误
        sys.stderr.write(f"Warning: Failed to load debug config: {e}\n")
    return False

DEBUG_MODE = _load_debug_mode()


def setup_logger(name="AI_GM", log_level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # 防止重复添加 handler
    if logger.handlers:
        return logger

    # WARNING+ 显示行号
    class ConditionalFormatter(logging.Formatter):
        def format(self, record):
            if record.levelno >= logging.WARNING:
                self._style._fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] [%(module)s:%(lineno)d] - %(message)s"
            else:
                self._style._fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s"
            return super().format(record)

    formatter = ConditionalFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 使用日期作为文件名
    today = datetime.now().strftime("%Y-%m-%d")
    file_handler = RotatingFileHandler(
        LOG_DIR / f"{today}.log",
        maxBytes=10*1024*1024, # 最大10MB
        # backupCount=5,
        encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

def get_logger(module_name: str, log_level: str = "INFO"):
    # 字符串到级别的映射
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }
    
    # 如果开启了调试模式，且请求的级别是 INFO，则提升为 DEBUG
    if DEBUG_MODE and log_level.upper() == "INFO":
        log_level = "DEBUG"
    
    # 转换字符串为级别，如果不存在则默认为 INFO
    actual_level = level_map.get(log_level.upper(), logging.INFO)
    
    return setup_logger(name=module_name, log_level=actual_level)
