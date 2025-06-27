from shared_lines import SharedLine, OneWaySharedLine, UnreliableSharedLine
from multiprocessing import Process, Event, Queue, Manager, set_start_method
from time import sleep, perf_counter

INITIAL_TIMESIZE = 10000  # milliseconds
INITIAL_DELAY_MS = 50     # milliseconds
timeout = 2               # seconds
expected_devices = 50     # Number of expected devices

# State constants
INIT = 0
WAIT = 1
NOTIFY_OTHERS = 2
WAIT_FOR_OTHERS = 3
SUCCESS = 4
ERROR = 5


def measure_high_time(wire):
    start = perf_counter()
    while wire.state():
        sleep(0.0001)
    return (perf_counter() - start) * 1000


class MCU:
    def __init__(self, name, lines_with_names):
        self.name = name
        self.interrupt_event = Event()
        self.interrupt_queue = Queue()
        self.lines = lines_with_names
        self.previous_states = {}
        self.stop_event = Event()

    def start(self):
        self.mcu_process = Process(target=self._run_mcu)
        self.peripheral_process = Process(target=self._run_peripheral)
        self.mcu_process.start()
        self.peripheral_process.start()

    def stop(self):
        self.stop_event.set()
        self.interrupt_event.set()

    def join(self, timeout=None):
        self.mcu_process.join(timeout)
        self.peripheral_process.join(timeout)

    def terminate(self):
        if self.mcu_process.is_alive():
            self.mcu_process.terminate()
        if self.peripheral_process.is_alive():
            self.peripheral_process.terminate()

    def _run_mcu(self):
        print(f"[{self.name}-MCU] Booting... waiting for events.")
        while not self.stop_event.is_set():
            self.interrupt_event.wait()
            if self.stop_event.is_set():
                break
            while not self.interrupt_queue.empty():
                irq_info = self.interrupt_queue.get()
                print(f"[{self.name}-MCU] Interrupt: Line '{irq_info['line']}' - {irq_info['edge']}")
                self._handle_interrupt(irq_info)
            self.interrupt_event.clear()

    def _handle_interrupt(self, info):
        print(f"[{self.name}-MCU] Handling: Line '{info['line']}' had a {info['edge']} edge.\n")

    def _run_peripheral(self):
        for name, line in self.lines:
            self.previous_states[name] = line.state()

        print(f"[{self.name}-Peripheral] Monitoring started.")
        while not self.stop_event.is_set():
            sleep(0.05)
            for name, line in self.lines:
                prev = self.previous_states[name]
                curr = line.state()
                if prev == 0 and curr == 1:
                    self._trigger_interrupt(name, "RISING")
                elif prev == 1 and curr == 0:
                    self._trigger_interrupt(name, "FALLING")
                self.previous_states[name] = curr

    def _trigger_interrupt(self, line_name, edge_type):
        irq_info = {"line": line_name, "edge": edge_type}
        self.interrupt_queue.put(irq_info)
        self.interrupt_event.set()


def toggle_lines(system_name, line_defs):
    sleep(1)
    for name, line, actor in line_defs:
        print(f"[{system_name}-TEST] Pulling '{name}' HIGH")
        line.pull_high(actor)
        sleep(1)
        print(f"[{system_name}-TEST] Releasing '{name}'")
        line.release(actor)
        sleep(1)
    print(f"[{system_name}-TEST] Done.")


if __name__ == "__main__":
    set_start_method("fork")  # wichtig für macOS/Linux

    manager = Manager()

    # Gemeinsame Leitungen für beide Systeme
    shared_line_1 = SharedLine(manager)
    shared_line_2 = UnreliableSharedLine(manager, failure_rate=0.2)

    # Beide Systeme teilen sich die Leitungen
    MCUA = MCU("A", [("shared_1", shared_line_1), ("shared_2", shared_line_2)])
    MCUB = MCU("B", [("shared_1", shared_line_1), ("shared_2", shared_line_2)])

    # Gemeinsame Toggler für dieselben Leitungen
    toggler = Process(target=toggle_lines, args=("COMMON", [
        ("shared_1", shared_line_1, "tester"),
        ("shared_2", shared_line_2, "tester"),
    ]))

    # Start Systeme & Test
    MCUA.start()
    MCUB.start()
    toggler.start()

    toggler.join()

    # MCUe beenden
    MCUA.stop()
    MCUB.stop()
    MCUA.join(timeout=0.5)
    MCUB.join(timeout=0.5)

    MCUA.terminate()
    MCUB.terminate()

    manager.shutdown()