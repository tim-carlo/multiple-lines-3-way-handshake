from multiprocessing import Process, Event, Queue, Manager, set_start_method
from time import sleep, perf_counter
import random
from ctypes import c_bool
from shared_lines import SharedLine

# Signal Timings (ms)
SYN_DURATION = 500
SYN_ACK_DURATION = 1000
ACK_DURATION = 1500
TOLERANCE = 20

# Time Slots in ms
TIME_SLOTS_MS = list(range(0, 1000, 10))

# FSM States
INIT = 0
INITIATOR = 1
RESPONDER = 2
SUCCESS = 3
FAILED = 4

MAYBE_RESPONDER = 5

class MCU:
    def __init__(self, name, line_names, manager):
        self.name = name
        self.manager = manager
        self.interrupt_queue = Queue()
        self.interrupt_event = Event()
        self.stop_event = Event()

        self.white_list = manager.list()
        self.black_list = manager.list()
        self.all_lines = {ln: obj for ln, obj in line_names}

        self.current_line = None
        self.state = manager.Value('i', INIT)
        self.role = manager.Value('u', '')

        self.successful_lines = manager.list()
        self.start_times = manager.dict()
        self.previous_states = {name: 0 for name, _ in line_names}
        self.last_sent_time = manager.Value('d', 0.0)

        self.received_syn = manager.Value(c_bool, False)
        self.received_syn_on = manager.Value('u', '')
        
        self.received_syn_ack = manager.Value(c_bool, False)
        self.received_syn_ack_on = manager.Value('u', '')
        
        self.received_ack = manager.Value(c_bool, False)
        self.received_ack_on = manager.Value('u', '')
        
        
        

    @property
    def current_line_obj(self):
        return self.all_lines.get(self.current_line)

    def start(self):
        self.p1 = Process(target=self._run_logic)
        self.p2 = Process(target=self._peripheral)
        self.p1.start()
        self.p2.start()

    def join(self):
        self.p1.join()
        self.p2.join()

    def stop(self):
        self.stop_event.set()
        self.interrupt_event.set()

    def _run_logic(self):
        tested = set()
        slot = None
        slot_start = None
        timeout = None
        responding_timeout = None

        while len(tested) < len(self.all_lines):
            self._process_interrupts()
            state = self.state.value

            if state == INIT:
                available = [ln for ln in self.all_lines.keys() if ln not in tested]
                if not available:
                    break

                self.current_line = random.choice(available)
                slot = random.choice(TIME_SLOTS_MS)
                slot_start = perf_counter()
                responding_timeout = None
                print(f"[{self.name}] Time slot: {slot} ms for line {self.current_line}", flush=True)

                while (perf_counter() - slot_start) < slot / 1000.0 and self.state.value == INIT:
                    self._process_interrupts()
                    # Check if any line is active and handle potential conflict
                    active_lines = [name for name, line in self.all_lines.items() if line.state() == 1]
                    if active_lines:
                        self.current_line = active_lines[0]  # Take the first active line
                        print(f"[{self.name}] Line active on {self.current_line}, entering MAYBE_RESPONDER state", flush=True)
                        self.state.value = MAYBE_RESPONDER
                        break
                    sleep(0.0001)
                    
                    
                if self.state.value != INIT:
                    continue

                if self.state.value != INIT:
                    print(f"[{self.name}] Interrupt processed, state is now {self.state.value}", flush=True)
                    continue
                
                self.current_line_obj.pull_high(self.name)
                print(f"[{self.name}] Send SYN on {self.current_line}", flush=True)
                sleep(SYN_DURATION / 1000.0)
                self.last_sent_time.value = perf_counter()
                self.current_line_obj.release(self.name)
                print(f"[{self.name}] SYN sent on {self.current_line}", flush=True)
               
                self.state.value = INITIATOR
                self.role.value = 'initiator'
                
            elif state == MAYBE_RESPONDER:
                responding_timeout = perf_counter() + 1.0  # Reduced timeout

                while perf_counter() < responding_timeout:
                    self._process_interrupts()
                    
                    # Check if we received a SYN signal
                    if self.received_syn.value:
                        print(f"[{self.name}] SYN received on {self.current_line}", flush=True)
                        self.received_syn.value = False
                        self.state.value = RESPONDER
                        self.received_syn_on.value = self.current_line
                        self.role.value = 'responder'
                        break
                    
                    # Check if line becomes inactive (potential false alarm)
                    # if self.current_line_obj and self.current_line_obj.state() == 0:
                    #     print(f"[{self.name}] Line {self.current_line} became inactive, false alarm", flush=True)
                    #     self._reset_state()
                    #     break
                        
                    sleep(0.0001)  # Increased sleep for better performance
                else:
                    if not self.stop_event.is_set():
                        print(f"[{self.name}] Timeout waiting for SYN on {self.current_line}, returning to INIT", flush=True)
                        self._reset_state()
                        
                    
            elif state == INITIATOR:
                timeout = perf_counter() + 4.0
                print(f"[{self.name}] Waiting for SYN_ACK on {self.current_line}", flush=True)

                while perf_counter() < timeout:
                    self._process_interrupts()
                    if self.received_syn_ack.value and self.received_syn_ack_on.value == self.current_line:
                        print(f"[{self.name}] SYN_ACK received on {self.current_line}", flush=True)
                        self.received_syn_ack.value = False
                        self.received_syn_ack_on.value = ''
                        
                        
                        # Send ACK
                        self.current_line_obj.pull_high(self.name)
                        sleep(ACK_DURATION / 1000.0)
                        self.last_sent_time.value = perf_counter()
                        self.current_line_obj.release(self.name)
                
                        self.state.value = SUCCESS
                        print(f"[{self.name}] ACK sent on {self.current_line}", flush=True)
                        break
                    sleep(0.0001)
                else:
                    print(f"[{self.name}] Timeout waiting for SYN_ACK on {self.current_line}", flush=True)
                    self.state.value = FAILED
                    self.received_syn_ack.value = False
                    self.received_syn_ack_on.value = ''

            elif state == RESPONDER:
                self.last_sent_time.value = perf_counter()
                
                print(f"[{self.name}] Send SYN_ACK on {self.current_line}", flush=True)
                self.current_line_obj.pull_high(self.name)
                sleep(SYN_ACK_DURATION / 1000.0)
                self.last_sent_time.value = perf_counter()
                self.current_line_obj.release(self.name)

                responding_timeout = perf_counter() + 4.0
                print(f"[{self.name}] Waiting for ACK on {self.current_line}", flush=True)

                while perf_counter() < responding_timeout:
                    self._process_interrupts()
                    if self.received_ack.value and self.received_ack_on.value == self.current_line:
                        self.received_ack.value = False
                        self.received_ack_on.value = ''
                        print(f"[{self.name}] ACK received on {self.current_line}", flush=True)
                        self.state.value = SUCCESS
                        break
                    sleep(0.001)
                else:
                    print(f"[{self.name}] Timeout waiting for ACK on {self.current_line}", flush=True)
                    self.received_ack.value = False
                    self.received_ack_on.value = ''
                    self.state.value = FAILED

            elif state == SUCCESS:
                print(f"[{self.name}] ✅ {self.current_line} works as {self.role.value}", flush=True)
                self.successful_lines.append(self.current_line)
                self.white_list.append(self.current_line)
                tested.add(self.current_line)
                self._reset_state()

            elif state == FAILED:
                print(f"[{self.name}] ❌ {self.current_line} failed as {self.role.value}", flush=True)
                self.black_list.append(self.current_line)
                tested.add(self.current_line)
                self._reset_state()
                sleep(0.005)

        print(f"[{self.name}] Successful lines: {list(self.successful_lines)}", flush=True)

    def _reset_state(self):
        self.state.value = INIT
        self.current_line = None
        self.received_syn_on.value = ''
        self.received_syn_ack.value = False
        self.received_ack.value = False
        self.received_ack_on.value = ''
        self.received_syn.value = False
        self.received_syn_ack_on.value = ''
        self.received_ack_on.value = ''
        self.last_sent_time.value = 0.0
        
        self.role.value = ''

    def _process_interrupts(self):
        while not self.interrupt_queue.empty():
            line_name, edge_type, duration = self.interrupt_queue.get()
            if abs(perf_counter() - self.last_sent_time.value) < 0.2:
                continue
                
            if edge_type == "SYN":
                print(f"[{self.name}] Received SYN on {line_name}", flush=True)
                self.received_syn.value = True
                self.current_line = line_name

            elif edge_type == "SYN_ACK":
                print(f"[{self.name}] Received SYN_ACK on {line_name}", flush=True)
                self.received_syn_ack.value = True
                self.received_syn_ack_on.value = line_name

            elif edge_type == "ACK":
                print(f"[{self.name}] Received ACK on {line_name} (Initiator)", flush=True)
                self.received_ack.value = True
                self.received_ack_on.value = line_name

    def _peripheral(self):
        durations = {name: None for name in self.all_lines.keys()}

        while not self.stop_event.is_set():
            for name, line in self.all_lines.items():
                state = line.state()
                prev = self.previous_states[name]

                if prev == 0 and state == 1:
                    durations[name] = perf_counter()
                elif prev == 1 and state == 0 and durations[name] is not None:
                    duration = (perf_counter() - durations[name]) * 1000
                    if abs(duration - SYN_DURATION) < TOLERANCE:
                        etype = "SYN"
                    elif abs(duration - SYN_ACK_DURATION) < TOLERANCE:
                        etype = "SYN_ACK"
                    elif abs(duration - ACK_DURATION) < TOLERANCE:
                        etype = "ACK"
                    else:
                        etype = None

                    if etype:
                        self.interrupt_queue.put((name, etype, duration))
                    durations[name] = None

                self.previous_states[name] = state
            sleep(0.001)

if __name__ == "__main__":
    set_start_method("fork")
    manager = Manager()
    lines = [("L1", SharedLine(manager)), ("L2", SharedLine(manager)), ("L3", SharedLine(manager))]

    mcu1 = MCU("A", lines, manager)
    mcu2 = MCU("B", lines, manager)

    mcu1.start()
    mcu2.start()

    try:
        sleep(30)
    finally:
        mcu1.stop()
        mcu2.stop()
        mcu1.join()
        mcu2.join()