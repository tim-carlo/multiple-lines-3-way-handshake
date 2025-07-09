from multiprocessing import Process, Manager, Event
from shared_lines import SharedLine
from time import sleep

class Pinger:
    def __init__(self, shared_line: SharedLine, name="Pinger", interval=1.0, pulse_width=0.1):
        self.shared_line = shared_line
        self.name = name
        self.interval = interval
        self.pulse_width = pulse_width

        self.stop_event = Event()
        self.p1 = None

    def _run_logic(self):
        try:
            while not self.stop_event.is_set():
                self.shared_line.pull_high(self.name)
                sleep(self.pulse_width)
                self.shared_line.release(self.name)
                sleep(self.interval - self.pulse_width)
        finally:
            self.shared_line.release(self.name)
            self.shared_line.log_end()

    def start(self):
        self.p1 = Process(target=self._run_logic)
        self.p1.start()

    def stop(self):
        self.stop_event.set()

    def join(self):
        if self.p1 is not None:
            self.p1.join()
            
class Bridge:
    def __init__(self, shared_lines: list[SharedLine], name="Bridge"):
        self.shared_lines = shared_lines
        self.name = name
        self.stop_event = Event()
        self.p1 = None

    def _run_logic(self):
        try:
            while not self.stop_event.is_set():
                high_lines = [line for line in self.shared_lines if line.is_high()]
                
                if len(high_lines) >= 1:
                    # if at least one line is high, pull high on all lines
                    for line in self.shared_lines:
                        if line not in high_lines:
                            line.pull_high(self.name)
                else:
                    # Either no lines or multiple lines are high, release all
                    for line in self.shared_lines:
                        line.release(self.name)
                
                sleep(0.0001)  # Small delay to prevent excessive CPU usage
        finally:
            for line in self.shared_lines:
                line.release(self.name)

    def start(self):
        self.p1 = Process(target=self._run_logic)
        self.p1.start()

    def stop(self):
        self.stop_event.set()

    def join(self):
        if self.p1 is not None:
            self.p1.join()