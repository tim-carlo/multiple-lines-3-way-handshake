from shared_lines import SharedLine, UnreliableSharedLine, OneWaySharedLine
from multiprocessing import Process, Event, Queue, Manager, set_start_method
from time import sleep, perf_counter
import random
from ctypes import c_bool, c_char_p

# Zustände
INIT = 0
RESPONDER = 1
INITIATOR = 2
ERROR_MODE = 3
SUCCESS = 4

# Parameter
SYN_DURATION = 0.050
ACK_DURATION = 0.100
FINAL_ACK_DURATION = 0.150
TIMEOUT = 5
MAX_ATTEMPTS = 10

# Zeitfenster (Slots)
TIME_SLOT_STEP_MS = 200
MAX_SLOT_MS = 1000
TIME_SLOTS_MS = list(range(0, MAX_SLOT_MS + 1, TIME_SLOT_STEP_MS))

class MCU:
    def __init__(self, name, lines_with_names, manager):
        self.name = name
        self.interrupt_event = Event()
        self.interrupt_queue = Queue()
        self.lines = lines_with_names
        self.previous_states = {}
        self.stop_event = Event()

        self.role = manager.Value(c_char_p, b"")
        self.handshake_successful = manager.Value(c_bool, False)
        self.syn_sent_time = manager.Value("d", 0.0)
        self.syn_received_time = manager.Value("d", 0.0)
        self.ack_received = manager.Value(c_bool, False)
        self.final_ack_received = manager.Value(c_bool, False)

        self.blacklist = manager.list()
        self.my_line_name, self.my_line = self._choose_line()

        self.state = manager.Value("i", INIT)

    def _choose_line(self):
        available = [(n, l) for n, l in self.lines if n not in self.blacklist]
        if not available:
            return random.choice(self.lines)
        return random.choice(available)

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
        for p in [self.mcu_process, self.peripheral_process]:
            if p.is_alive():
                p.terminate()

    def _run_mcu(self):
        print(f"[{self.name}-MCU] Booting... using line: {self.my_line_name}", flush=True)

        attempt = 0
        while not self.stop_event.is_set() and attempt < MAX_ATTEMPTS:
            self.syn_sent_time.value = 0.0
            self.syn_received_time.value = 0.0
            self.ack_received.value = False
            self.final_ack_received.value = False
            self.role.value = b""
            self.handshake_successful.value = False
            self.state.value = INIT

            slot_ms = random.choice(TIME_SLOTS_MS)
            print(f"[{self.name}] Slot ausgewählt: {slot_ms} ms", flush=True)
            sleep(slot_ms / 1000.0)

            if self.state.value == INIT:
                print(f"[{self.name}] Versuch {attempt + 1}: sende SYN", flush=True)
                self.state.value = INITIATOR
                self.syn_sent_time.value = perf_counter()
                self.my_line.pull_high(self.name)
                sleep(SYN_DURATION)
                self.my_line.release(self.name)

            timeout_timer = perf_counter()
            while perf_counter() - timeout_timer < TIMEOUT:
                # Verarbeite eingehende Interrupts
                while not self.interrupt_queue.empty():
                    irq_info = self.interrupt_queue.get()
                    self._handle_interrupt(irq_info)

                if self.state.value == RESPONDER:
                    if self.role.value != b"responder":
                        self.role.value = b"responder"
                        line = dict(self.lines)[self.received_line_name]
                        sleep(random.uniform(0.01, 0.05))
                        line.pull_high(self.name)
                        sleep(ACK_DURATION)
                        line.release(self.name)

                elif self.state.value == INITIATOR and self.ack_received.value:
                    self.role.value = b"initiator"
                    sleep(0.05)
                    self.my_line.pull_high(self.name)
                    sleep(FINAL_ACK_DURATION)
                    self.my_line.release(self.name)
                    self.handshake_successful.value = True
                    self.state.value = SUCCESS
                    break

                elif self.state.value == RESPONDER and self.final_ack_received.value:
                    self.handshake_successful.value = True
                    self.state.value = SUCCESS
                    break

                sleep(0.01)

            if self.handshake_successful.value:
                print(f"[{self.name}] ✅ Handshake erfolgreich als {self.role.value.decode()}.", flush=True)
                return
            else:
                print(f"[{self.name}] ❌ Handshake fehlgeschlagen, Leitung blacklisten: {self.my_line_name}", flush=True)
                if self.my_line_name not in self.blacklist:
                    self.blacklist.append(self.my_line_name)
                self.my_line_name, self.my_line = self._choose_line()
                attempt += 1
                self.state.value = ERROR_MODE

        print(f"[{self.name}] ⛔ Handshake gescheitert nach {MAX_ATTEMPTS} Versuchen.", flush=True)

    def _handle_interrupt(self, info):
        now = perf_counter()
        line_name = info['line']
        edge_type = info['edge']

        if edge_type == "SYN":
            if self.state.value == INITIATOR:
                delta = now - self.syn_sent_time.value
                if delta < 0.020:
                    print(f"[{self.name}] (IGNORIERT) Eigener SYN erkannt auf {line_name} (Δ={delta*1000:.1f} ms)", flush=True)
                    return
            if self.syn_received_time.value == 0.0:
                self.syn_received_time.value = now
                self.state.value = RESPONDER
                self.received_line_name = line_name
                print(f"[{self.name}] SYN empfangen von {line_name}", flush=True)

        elif edge_type == "ACK":
            if self.role.value == b"responder":
                delta = now - self.syn_received_time.value
                if delta < 0.020:
                    print(f"[{self.name}] (IGNORIERT) Eigener ACK erkannt (Δ={delta*1000:.1f} ms)", flush=True)
                    return
            self.ack_received.value = True
            print(f"[{self.name}] ACK empfangen", flush=True)

        elif edge_type == "FINAL_ACK":
            if self.role.value == b"initiator":
                delta = now - self.syn_sent_time.value
                if delta < 0.050:
                    print(f"[{self.name}] (IGNORIERT) Eigener FINAL_ACK erkannt (Δ={delta*1000:.1f} ms)", flush=True)
                    return
            self.final_ack_received.value = True
            print(f"[{self.name}] FINAL_ACK empfangen", flush=True)

    def _run_peripheral(self):
        start_times = {name: None for name, _ in self.lines}
        self.previous_states = {name: line.state() for name, line in self.lines}
        while not self.stop_event.is_set():
            sleep(0.01)
            for name, line in self.lines:
                prev = self.previous_states[name]
                curr = line.state()
                if prev == 0 and curr == 1:
                    start_times[name] = perf_counter()
                elif prev == 1 and curr == 0 and start_times[name] is not None:
                    duration_ms = (perf_counter() - start_times[name]) * 1000
                    if abs(duration_ms - 50) < 20:
                        edge_type = "SYN"
                    elif abs(duration_ms - 100) < 30:
                        edge_type = "ACK"
                    elif abs(duration_ms - 150) < 30:
                        edge_type = "FINAL_ACK"
                    else:
                        edge_type = f"DURATION_{duration_ms:.1f}ms"
                    self._trigger_interrupt(name, edge_type)
                    start_times[name] = None
                self.previous_states[name] = curr

    def _trigger_interrupt(self, line_name, edge_type):
        irq_info = {"line": line_name, "edge": edge_type}
        self.interrupt_queue.put(irq_info)
        self.interrupt_event.set()

if __name__ == "__main__":
    set_start_method("fork")
    manager = Manager()

    lines = [
        ("L1", SharedLine(manager)),
        ("L2", SharedLine(manager)),
        ("L4", SharedLine(manager)),
    ]

    mcu_a = MCU("A", lines, manager)
    mcu_b = MCU("B", lines, manager)

    mcu_a.start()
    mcu_b.start()

    try:
        sleep(20)
    except KeyboardInterrupt:
        pass

    mcu_a.stop()
    mcu_b.stop()
    mcu_a.join()
    mcu_b.join()
    mcu_a.terminate()
    mcu_b.terminate()

    print("\n[ERGEBNISSE]")
    print(f"{mcu_a.name}: Rolle = {mcu_a.role.value.decode()}, Erfolg = {mcu_a.handshake_successful.value}")
    print(f"{mcu_b.name}: Rolle = {mcu_b.role.value.decode()}, Erfolg = {mcu_b.handshake_successful.value}")

    manager.shutdown()