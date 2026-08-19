import os
import sys
import logging
import threading
from rich.console import Console
from rich.logging import RichHandler
from pythonjsonlogger.jsonlogger import JsonFormatter

class Logger:
    _lock: threading.Lock = threading.Lock()
    _handler_added: bool = False

    def __init__(
        self,
        name: str = __name__,
        level: int = logging.INFO,
        stream=sys.stdout,
    ) -> None:
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        
        with Logger._lock:
            if not Logger._handler_added:
                self.logger.handlers.clear()
                if os.getenv("ENV") == "local":
                    handler: logging.Handler = RichHandler(
                        console=Console(
                            file=stream,
                            color_system="truecolor",
                            force_terminal=True,
                        ),
                        show_path=True,
                        markup=True,
                        rich_tracebacks=True,
                        tracebacks_show_locals=False,
                        show_time=False,
                    )
                    handler.setFormatter(
                        logging.Formatter(
                            "%(asctime)s - %(message)s"
                        )
                    )
                else:
                    handler = logging.StreamHandler(stream)
                    formatter = JsonFormatter(
                        "%(asctime)s - %(levelname)s - %(name)s - %(filename)s - %(lineno)d - %(message)s"
                    )
                    
                self.logger.addHandler(handler)
                Logger._handler_added = True
    
    def get_logger(self) -> logging.Logger:
        return self.logger