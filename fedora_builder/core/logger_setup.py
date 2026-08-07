import logging
import sys

def supports_color():
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG: '\033[94m',    # Blue
        logging.INFO: '\033[92m',     # Green
        logging.WARNING: '\033[93m',  # Yellow
        logging.ERROR: '\033[91m',    # Red
        logging.CRITICAL: '\033[95m'  # Magenta
    }
    RESET = '\033[0m'

    def format(self, record):
        color = self.COLORS.get(record.levelno, self.RESET)
        if supports_color():
            record.levelname = f"{color}{record.levelname}{self.RESET}"
            record.msg = f"{color}{record.msg}{self.RESET}"
        return super().format(record)

def setup_logger(name, level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)
        formatter = ColorFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger
