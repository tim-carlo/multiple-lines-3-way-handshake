from shared_lines import SharedLine, UnreliableSharedLine, OneWaySharedLine
from multiprocessing import Process, Event, Queue, Manager, set_start_method
from time import sleep, perf_counter
import random
from ctypes import c_bool, c_char_p


# Parameter
MAX_START_DELAY = 10     # Sekunden
SYN_DURATION = 0.050     # Sekunden (50ms)
ACK_DURATION = 0.100     # Sekunden (100ms)
TIMEOUT = 5              # Sekunden (Timeout für ACK)


class MCU:
    def __init__(self, name, lines_with_names, manager):
        self.name = name
        self.interrupt_event = Event()
        self.interrupt_queue = Queue()
        self.lines = lines_with_names
        self.previous_states = {}
        self.stop_event = Event()

        # Gemeinsame Statuswerte
        self.role = manager.Value(c_char_p, b"")
        self.handshake_successful = manager.Value(c_bool, False)
        self.syn_sent_time = manager.Value("d", 0.0)
        self.syn_received_time = manager.Value("d", 0.0)
        self.ack_received = manager.Value(c_bool, False)

        self.my_line_name, self.my_line = random.choice(self.lines)

    def start(self):
        self.mcu_process = Process(target=self._run_mcu)
        self.peripheral_process = Process(target=self._run_peripheral)
        self.signal_process = Process(target=self._signal_logic)
        self.mcu_process.start()
        self.peripheral_process.start()
        self.signal_process.start()

    def stop(self):
        self.stop_event.set()
        self.interrupt_event.set()

    def join(self, timeout=None):
        self.mcu_process.join(timeout)
        self.peripheral_process.join(timeout)
        self.signal_process.join(timeout)

    def terminate(self):
        for p in [self.mcu_process, self.peripheral_process, self.signal_process]:
            if p.is_alive():
                p.terminate()

    def _run_mcu(self):
        print(f"[{self.name}-MCU] Booting... will use line: {self.my_line_name}", flush=True)
        while not self.stop_event.is_set():
            self.interrupt_event.wait()
            if self.stop_event.is_set():
                break
            while not self.interrupt_queue.empty():
                irq_info = self.interrupt_queue.get()
                print(f"[{self.name}-MCU] Interrupt: Line '{irq_info['line']}' - {irq_info['edge']}", flush=True)
                self._handle_interrupt(irq_info)
            self.interrupt_event.clear()

    def _handle_interrupt(self, info):
        now = perf_counter()
        if info['edge'] == "INITIAL":
            if self.syn_received_time.value == 0.0:
                self.syn_received_time.value = now
                print(f"[{self.name}-MCU] SYN empfangen von {info['line']}", flush=True)
                # Nur ACK senden, wenn man vorher kein SYN gesendet hat
                if self.syn_sent_time.value == 0.0:
                    self.role.value = b"responder"
                    line = dict(self.lines)[info['line']]
                    sleep(random.uniform(0.01, 0.05))
                    line.pull_high(self.name)
                    sleep(ACK_DURATION)
                    line.release(self.name)

        elif info['edge'] == "ACK":
            self.ack_received.value = True
            print(f"[{self.name}-MCU] ACK empfangen", flush=True)

    def _run_peripheral(self):
        start_times = {name: None for name, _ in self.lines}
        self.previous_states = {name: line.state() for name, line in self.lines}
        print(f"[{self.name}-Peripheral] Monitoring started.", flush=True)
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
                        edge_type = "INITIAL"
                    elif abs(duration_ms - 100) < 30:
                        edge_type = "ACK"
                    else:
                        edge_type = f"DURATION_{duration_ms:.1f}ms"
                    self._trigger_interrupt(name, edge_type)
                    start_times[name] = None
                self.previous_states[name] = curr

    def _trigger_interrupt(self, line_name, edge_type):
        irq_info = {"line": line_name, "edge": edge_type}
        self.interrupt_queue.put(irq_info)
        self.interrupt_event.set()

    def _signal_logic(self):
        delay = random.uniform(0, MAX_START_DELAY)
        print(f"[{self.name}] Warte {delay:.2f}s vor möglichem SYN", flush=True)
        sleep(delay)

        # Nur senden, wenn vorher kein SYN empfangen wurde
        if self.syn_received_time.value == 0.0:
            print(f"[{self.name}] Sende SYN auf {self.my_line_name}", flush=True)
            self.syn_sent_time.value = perf_counter()
            self.my_line.pull_high(self.name)
            sleep(SYN_DURATION)
            self.my_line.release(self.name)

        # Warten auf ACK
        print(f"[{self.name}] Warte auf ACK...", flush=True)
        start = perf_counter()
        while (perf_counter() - start) < TIMEOUT:
            if self.ack_received.value:
                self.handshake_successful.value = True
                break
            sleep(0.01)

        # Rolle bestimmen, falls noch nicht geschehen
        if self.role.value == b"":
            s_sent = self.syn_sent_time.value
            s_recv = self.syn_received_time.value
            if s_sent > 0 and s_recv > 0:
                self.role.value = b"initiator" if s_sent < s_recv else b"responder"
            elif s_sent > 0:
                self.role.value = b"initiator"
            elif s_recv > 0:
                self.role.value = b"responder"
            else:
                self.role.value = b"unknown"

        # Ausgabe
        if self.handshake_successful.value:
            print(f"[{self.name}] ✅ Handshake erfolgreich als {self.role.value.decode()}.", flush=True)
        else:
            print(f"[{self.name}] ❌ Handshake fehlgeschlagen als {self.role.value.decode()}.", flush=True)
            
if __name__ == "__main__":
    set_start_method("fork")  # unter Windows ggf. "spawn"

    manager = Manager()

    shared_line_1 = UnreliableSharedLine(manager, failure_rate=0.1)  # 10% Ausfallrate
    shared_line_2 = SharedLine(manager)
    shared_line_3 = OneWaySharedLine(manager, "A")  # Einweg-Leitung von A zu B
    shared_line_4 = OneWaySharedLine(manager, "B")  # Einweg-Leitung von B zu A
    shared_line_5 = SharedLine(manager)  # Zusätzliche gemeinsame Leitung
    shared_line_6 = SharedLine(manager)  # Weitere gemeinsame Leitung
    shared_line_7 = UnreliableSharedLine(manager, failure_rate=0.2)  # 20% Ausfallrate

    # Zwei MCUs mit synchronisierten Statuswerten
    mcu_a = MCU("A", [("Line1", shared_line_1), ("Line2", shared_line_2), ("Line3", shared_line_3), ("Line4", shared_line_4)], manager)
    mcu_b = MCU("B", [("Line1", shared_line_1), ("Line2", shared_line_2), ("Line3", shared_line_3), ("Line4", shared_line_4), ("Line5", shared_line_5), ("Line6", shared_line_6), ("Line7", shared_line_7)], manager)

    # Starten
    mcu_a.start()
    mcu_b.start()

    try:
        sleep(15)
    except KeyboardInterrupt:
        pass

    # Stoppen & Warten
    mcu_a.stop()
    mcu_b.stop()
    mcu_a.join()
    mcu_b.join()
    mcu_a.terminate()
    mcu_b.terminate()

    # Ergebnisse anzeigen
    print("\n[ERGEBNISSE]")
    print(f"{mcu_a.name}: Rolle = {mcu_a.role.value.decode()}, Erfolg = {mcu_a.handshake_successful.value}")
    print(f"{mcu_b.name}: Rolle = {mcu_b.role.value.decode()}, Erfolg = {mcu_b.handshake_successful.value}")

    manager.shutdown()