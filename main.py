from multiprocessing import Process, Event, Queue, Manager, set_start_method
from time import sleep, perf_counter
import random
from ctypes import c_bool
from shared_lines import SharedLine, OneWaySharedLine, UnreliableSharedLine, MultiLinePlotter

# Signal Timings (ms)
SYN_DURATION = 500
SYN_ACK_DURATION = 1000
ACK_DURATION = 1500
TOLERANCE = 100

LINE_SETTLE_DURATION = 50  # Duration to wait for line to settle after pulling high

# Time Slots in ms
TIME_SLOTS_MS = list(range(0, 1000, 10))

# FSM States
INIT = 0
INITIATOR = 1
RESPONDER = 2
SUCCESS = 3
FAILED = 4
MAYBE_RESPONDER = 5
PASSIVE_RESPONDER = 6  # New state for when all pins have been tested


TIMEOUT_RESPONDER = 2.0  # Timeout for responder to wait for SYN
TIMEOUT_SYN_ACK = 2.0  # Timeout for initiator to wait for SYN_ACK
TIMEOUT_ACK = 2.0  # Timeout for responder to wait for ACK
PASSIVE_RESPONDER_TIMEOUT = 10.0  # Timeout for passive responder mode


class PinData:
    def __init__(self):
        self.ack = False
        self.syn = False
        self.syn_ack = False
        self.role = ''
        
    def set_ack(self, value):
        self.ack = value
    def set_syn(self, value):
        self.syn = value
    def set_syn_ack(self, value):
        self.syn_ack = value
    def set_role(self, value):
        self.role = value
    def finished(self):
        return self.role != ''




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
        
        self.set_curent_line = manager.Value(c_bool, False)
        

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
        print(f"TEST: {self.name} starting logic with {len(self.all_lines)} lines")
        
        tested = set()
        slot = None
        slot_start = None
        timeout = None
        responding_timeout = None
        
        passive_timeout = 0

        while True:
            self._process_interrupts()
            state = self.state.value

            if state == INIT:
                available = [ln for ln in self.all_lines.keys() if ln not in tested]
                if not available:
                    # All pins tested, enter passive responder mode
                    print(f"[{self.name}] All pins tested, entering PASSIVE_RESPONDER mode", flush=True)
                    self.state.value = PASSIVE_RESPONDER
                    continue

                self.current_line = random.choice(available)
                slot = random.choice(TIME_SLOTS_MS)
                slot_start = perf_counter()
                responding_timeout = None
                print(f"[{self.name}] Time slot: {slot} ms for line {self.current_line}", flush=True)

                while (perf_counter() - slot_start) < slot / 1000.0 and self.state.value == INIT:
                    #self._process_interrupts()
                    # Check if any line is active and handle potential conflict
                    active_lines = [name for name, line in self.all_lines.items() if line.state() == 1]
                    if active_lines:
                        self.current_line = active_lines[0]  # Take the first active line
                        print(f"[{self.name}] Line active on {self.current_line}, entering MAYBE_RESPONDER state", flush=True)
                        self.state.value = MAYBE_RESPONDER
                        break
                    sleep(0.0001)
                    

                if self.state.value != INIT:
                    print(f"[{self.name}] Interrupt processed, state is now {self.state.value}", flush=True)
                    continue
                
                
                self.set_curent_line.value = True
                print(f"[{self.name}] Send SYN on {self.current_line}", flush=True)
                self.current_line_obj.pull_high(self.name)
                
                syn_start = perf_counter()
                syn_end = syn_start + (SYN_DURATION / 1000.0)
                
                confict_detected = False
                
                while perf_counter() < syn_end:
                    # Check if any other line is high during SYN transmission
                    other_active_lines = [name for name, line in self.all_lines.items() 
                                        if name != self.current_line and line.state() == 1]
                    if other_active_lines:
                        # Conflict detected, abort SYN and switch to responder
                        self.current_line_obj.release(self.name)
                        self.set_curent_line.value = False
                        
                        self.current_line = other_active_lines[0]
                        print(f"[{self.name}] Conflict detected on {self.current_line}, switching to MAYBE_RESPONDER", flush=True)
                        self.state.value = MAYBE_RESPONDER
                        confict_detected = True
                        
                        break
                if not confict_detected:
                    # SYN completed successfully
                    self.last_sent_time.value = perf_counter()
                    self.current_line_obj.release(self.name)
                    print(f"[{self.name}] SYN sent on {self.current_line}", flush=True)
    
                    self.state.value = INITIATOR
                    self.role.value = 'initiator'

            elif state == PASSIVE_RESPONDER:
                
                if perf_counter() > passive_timeout:
                    print("finishing passive responder")
                    break
                self._process_interrupts()
                
                # Check if any line is active
                active_lines = [name for name, line in self.all_lines.items() if line.state() == 1]
                if active_lines:
                    self.current_line = active_lines[0]
                    print(f"[{self.name}] Line active on {self.current_line}, entering MAYBE_RESPONDER state", flush=True)
                    self.state.value = MAYBE_RESPONDER
                    break

                # Check if we received a SYN signal
                if self.received_syn.value:
                    self.current_line = self.received_syn_on.value
                    print(f"[{self.name}] SYN received on {self.current_line}, switching to RESPONDER", flush=True)
                    self.received_syn.value = False
                    self.state.value = RESPONDER
                    self.role.value = 'responder'
                    break
                    
                
            elif state == MAYBE_RESPONDER:
                responding_timeout = perf_counter() + TIMEOUT_RESPONDER
                
                has_seen_signal = False

                while perf_counter() < responding_timeout:
                    self._process_interrupts()

                    # Check if any other line is high during MAYBE_RESPONDER state
                    if self.current_line and self.current_line_obj and self.current_line_obj.state() == 1:
                        if not has_seen_signal:
                            print(f"[{self.name}] Line {self.current_line} is high, waiting for SYN or SYN_ACK", flush=True)
                            responding_timeout = responding_timeout + (SYN_DURATION + TOLERANCE) / 1000.0
                            has_seen_signal = True
            
                    if self.received_syn.value:
                        self.current_line = self.received_syn_on.value  # Use the line on which SYN was received
                        print(f"[{self.name}] SYN received on {self.current_line}, switching to RESPONDER", flush=True)
                        self.received_syn.value = False
                        self.state.value = RESPONDER
                        self.role.value = 'responder'
                        break
                else:
                    print(f"[{self.name}] Timeout waiting for SYN on {self.current_line}, returning to INIT", flush=True)
                    self._reset_state()
                        
                    
            elif state == INITIATOR:
                timeout = perf_counter() + TIMEOUT_SYN_ACK
                print(f"[{self.name}] Waiting for SYN_ACK on {self.current_line}", flush=True)
                
                has_seen_signal = False

                while perf_counter() < timeout:
                    self._process_interrupts()
                    
                    # Check if any other line is high during INITIATOR state
                    other_active_lines = [name for name, line in self.all_lines.items() 
                                        if name != self.current_line and line.state() == 1]
                    if other_active_lines:
                        self.current_line = other_active_lines[0]
                        print(f"[{self.name}] Other line {self.current_line} is high, switching to MAYBE_RESPONDER", flush=True)
                        self.state.value = MAYBE_RESPONDER
                        break
                    
                    if self.current_line and self.current_line_obj and self.current_line_obj.state() == 1:
                        if not has_seen_signal:
                            print(f"[{self.name}] Line {self.current_line} is high, waiting for SYN_ACK", flush=True)
                            print(f"[{self.name}] Timeout set to {timeout}", flush=True)
                            timeout = timeout + (SYN_ACK_DURATION + TOLERANCE) / 1000.0
                            print(f"[{self.name}] Timeout extended to {timeout}", flush=True)
                            has_seen_signal = True
                    
                    if self.received_syn_ack.value and self.received_syn_ack_on.value == self.current_line:
                        print(f"[{self.name}] SYN_ACK received on {self.current_line}", flush=True)
                        self.received_syn_ack.value = False
                        self.received_syn_ack_on.value = ''
                        
                        
                        sleep(LINE_SETTLE_DURATION / 1000.0)  # Wait for line to settle
                        
                        # Send ACK
                        self.set_curent_line.value = True
                        self.current_line_obj.pull_high(self.name)
                        sleep(ACK_DURATION / 1000.0)
                        self.last_sent_time.value = perf_counter()
                        self.current_line_obj.release(self.name)
                        self.set_curent_line.value = False
                
                        self.state.value = SUCCESS
                        print(f"[{self.name}] ACK sent on {self.current_line}", flush=True)
                        break
                else:
                    print(f"[{self.name}] Timeout waiting for SYN_ACK on {self.current_line}", flush=True)
                    self.state.value = FAILED
                    self.received_syn_ack.value = False
                    self.received_syn_ack_on.value = ''

            elif state == RESPONDER:
                self.last_sent_time.value = perf_counter()
                
               
                
                self.set_curent_line.value = True
                self.current_line_obj.pull_high(self.name)
                sleep(SYN_ACK_DURATION / 1000.0)
                self.last_sent_time.value = perf_counter()
                self.current_line_obj.release(self.name)
                self.set_curent_line.value = False
                print(f"[{self.name}] Send SYN_ACK on {self.current_line}", flush=True)
                
                sleep(LINE_SETTLE_DURATION / 1000.0)  # Wait for line to settle

                responding_timeout = perf_counter() + TIMEOUT_ACK
                print(f"[{self.name}] Waiting for ACK on {self.current_line}", flush=True)
                
                has_seen_signal = False

                while perf_counter() < responding_timeout:
                    self._process_interrupts()
                    
                    
                    # Check if the current line is still high
                    if self.current_line and self.current_line_obj and self.current_line_obj.state() == 1:
                        if not has_seen_signal:
                            print(f"[{self.name}] Line {self.current_line} is high, waiting for ACK", flush=True)
                            responding_timeout = responding_timeout + (ACK_DURATION + TOLERANCE) / 1000.0
                            has_seen_signal = True
                    if self.received_ack.value and self.received_ack_on.value == self.current_line:
                        self.received_ack.value = False
                        self.received_ack_on.value = ''
                        print(f"[{self.name}] ACK received on {self.current_line}", flush=True)
                        self.state.value = SUCCESS
                        break
                else:
                    print(f"[{self.name}] Timeout waiting for ACK on {self.current_line}", flush=True)
                    self.received_ack.value = False
                    self.received_ack_on.value = ''
                    self.state.value = FAILED

            elif state == SUCCESS:
                print(f"[{self.name}] ✅ {self.current_line} works as {self.role.value}", flush=True)
                if self.current_line not in self.white_list:
                    self.white_list.append(self.current_line)
                if self.current_line in self.black_list:
                    self.black_list.remove(self.current_line)
                self.successful_lines.append(self.current_line)
                tested.add(self.current_line)
                
                # After success, check if all pins are tested
                if len(tested) >= len(self.all_lines):
                    print(f"[{self.name}] Successful lines: {list(self.successful_lines)}", flush=True)
                    print(f"[{self.name}] Blacklisted lines: {list(self.black_list)}", flush=True)
                    print(f"[{self.name}] All pins tested after success, entering PASSIVE_RESPONDER", flush=True)
                    self._reset_state()
                    
                    passive_timeout = perf_counter() + PASSIVE_RESPONDER_TIMEOUT
                    self.state.value = PASSIVE_RESPONDER
                else:
                    self._reset_state()

            elif state == FAILED:
                print(f"[{self.name}] ❌ {self.current_line} failed as {self.role.value}", flush=True)
                if self.current_line not in self.black_list:
                    self.black_list.append(self.current_line)
                if self.current_line in self.white_list:
                    self.white_list.remove(self.current_line)
                tested.add(self.current_line)
                
                # After failure, check if all pins are tested
                if len(tested) >= len(self.all_lines):
                    print(f"[{self.name}] Successful lines: {list(self.successful_lines)}", flush=True)
                    print(f"[{self.name}] Blacklisted lines: {list(self.black_list)}", flush=True)
                    print(f"[{self.name}] All pins tested after failure, entering PASSIVE_RESPONDER", flush=True)
                    self._reset_state()
                    
                    passive_timeout = perf_counter() + PASSIVE_RESPONDER_TIMEOUT
                    self.state.value = PASSIVE_RESPONDER
                else:
                    self._reset_state()

        

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
                self.received_syn_on.value = line_name
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
                    print(f"[{self.name}] Line {name} pulled low after {duration:.2f} ms", flush=True)
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

if __name__ == "__main__":
    set_start_method("fork")
    manager = Manager()
    lines = [("L1", SharedLine(manager)), ("L2", SharedLine(manager)), ("L3", SharedLine(manager))]
    
    
    # Create shared lines
    shared_lines = {
        "L1": SharedLine(manager),
        "L2": SharedLine(manager), 
        "L3": SharedLine(manager),
        "L4": SharedLine(manager),
        "L5": SharedLine(manager),
        "L6": SharedLine(manager),
        "L7": SharedLine(manager),
        "L8": SharedLine(manager),
        "L9": SharedLine(manager),
        "L10": SharedLine(manager),
    }
    
    
    # Define lines for each controller - various connectivity scenarios
    
    # Scenario 1: Partially overlapping lines (current scenario)
    # lines_controller1 = [
    #   ("L1", shared_lines["L1"]),
    #   ("L2", shared_lines["L2"]),
    #   ("L3", shared_lines["L3"]),
    #   ("L4", shared_lines["L4"]),
    #   ("L5", shared_lines["L5"]),
    #   ("L6", shared_lines["L6"]),
    # ]
    
    # lines_controller2 = [
    #   ("L1", shared_lines["L1"]),
    #   ("L2", shared_lines["L2"]),
    #   ("L3", shared_lines["L3"]),
    #   ("L4", shared_lines["L4"]),
    #   ("L5", shared_lines["L5"]), 
    #   ("L7", shared_lines["L7"]),
    # ]
    
    # Scenario 2: Completely separate lines (no overlap)
    # lines_controller1 = [
    #   ("L1", shared_lines["L1"]),
    #   ("L2", shared_lines["L2"]),
    #   ("L3", shared_lines["L3"]),
    #   ("L4", shared_lines["L4"]),
    #   ("L5", shared_lines["L5"]),
    # ]
    
    # lines_controller2 = [
    #   ("L6", shared_lines["L6"]),
    #   ("L7", shared_lines["L7"]),
    #   ("L8", shared_lines["L8"]),
    #   ("L9", shared_lines["L9"]),
    #   ("L10", shared_lines["L10"]),
    # ]
    
    # Scenario 3: Single shared line
    # lines_controller1 = [
    #   ("L1", shared_lines["L1"]),
    #   ("L2", shared_lines["L2"]),
    #   ("L3", shared_lines["L3"]),
    # ]
    
    # lines_controller2 = [
    #   ("L1", shared_lines["L1"]),
    #   ("L4", shared_lines["L4"]),
    #   ("L5", shared_lines["L5"]),
    # ]
    
    # Scenario 4: Asymmetric connection (one controller has more lines)
    # lines_controller1 = [
    #   ("L1", shared_lines["L1"]),
    #   ("L2", shared_lines["L2"]),
    #   ("L3", shared_lines["L3"]),
    #   ("L4", shared_lines["L4"]),
    #   ("L5", shared_lines["L5"]),
    #   ("L6", shared_lines["L6"]),
    #   ("L7", shared_lines["L7"]),
    #   ("L8", shared_lines["L8"]),
    # ]
    
    # lines_controller2 = [
    #   ("L1", shared_lines["L1"]),
    #   ("L3", shared_lines["L3"]),
    #   ("L5", shared_lines["L5"]),
    # ]
    
    # Scenario 5: Cross-wired connections
    lines_controller1 = [
      ("L1", shared_lines["L1"]),
      ("L2", shared_lines["L3"]),  # Cross-wired
      ("L3", shared_lines["L2"]),  # Cross-wired
      ("L4", shared_lines["L4"]),
    ]
    
    lines_controller2 = [
      ("L1", shared_lines["L1"]),
      ("L2", shared_lines["L2"]),
      ("L3", shared_lines["L3"]),
      ("L4", shared_lines["L5"]),  # Different line
     ]
    
    mcu1 = MCU("A", lines_controller1, manager)
    mcu2 = MCU("B", lines_controller2, manager)

    mcu1.start()
    mcu2.start()

    try:
        sleep(20)
    finally:
        mcu1.stop()
        mcu2.stop()
        mcu1.join()
        mcu2.join()
    
    # Log end state for all lines
    for line in shared_lines.values():
        line.log_end()
    # Plotting
    plotter = MultiLinePlotter([])
    
    
    #Add all lines to the plotter
    for name, line in shared_lines.items():
        plotter.add_line(line)
    
    
    plotter.plot_all()
    