import random
from multiprocessing import Process, Manager, Queue, Event, Value
from time import sleep, perf_counter
from ctypes import c_bool

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

TIMEOUT_RESPONDER = 2.0  # Timeout for responder to wait for SYN
TIMEOUT_SYN_ACK = 2.0  # Timeout for initiator to wait for SYN_ACK
TIMEOUT_ACK = 2.5  # Timeout for responder to wait for ACK


MAXIMUM_NUMBER_OF_FALSE_RESPONSES = 2  # Maximum number of false responses before giving up

ERROR_REASON_BLACKLISTED = 'blacklisted'
ERROR_REASON_DISTURBED = 'disturbed'
ERROR_REASON_TIMEOUT = 'timeout'





class PinData:
    def __init__(self, name, line=None):
        self.line = line  # Reference to the shared line object, if needed
        self.ack = False
        self.syn = False
        self.syn_ack = False
        self.role = ''
        self.name = name
        self.num_false_responses = 0  # Counter for false responses
        
        self.blacklisted = False # Flag to indicate if this pin is blacklisted
        self.successful = False  # Flag to indicate if this pin has been successfully tested
        self.error_reason = None  # Reason for failure, if any
        
    def set_ack(self, value):
        self.ack = value
    def set_syn(self, value):
        self.syn = value
    def set_syn_ack(self, value):
        self.syn_ack = value
        
    def set_role(self, value):
        self.role = value
    
    def set_blacklisted(self, value):
        self.blacklisted = value
        self.error_reason = ERROR_REASON_BLACKLISTED if value else None
    def set_successful(self, value):
        self.successful = value
    
    def increment_false_responses(self):
        self.num_false_responses += 1
        if self.num_false_responses >= MAXIMUM_NUMBER_OF_FALSE_RESPONSES:
            self.error_reason = ERROR_REASON_DISTURBED
        
    
    def is_blacklisted(self):
        return self.num_false_responses >= MAXIMUM_NUMBER_OF_FALSE_RESPONSES or self.blacklisted
    
    def is_tested(self):
        return self.successful or self.is_blacklisted()
    
    def to_dict(self):
       return {
            'name': self.name,
            'ack': self.ack,
            'syn': self.syn,
            'syn_ack': self.syn_ack,
            'role': self.role,
            'num_false_responses': self.num_false_responses,
            'blacklisted': self.blacklisted,
            'successful': self.successful,
            'error_reason': self.error_reason
        }
    
    def __eq__(self, other):
        if not isinstance(other, PinData):
            return False
        return (self.name == other.name and 
                self.ack == other.ack and 
                self.syn == other.syn and 
                self.syn_ack == other.syn_ack and 
                self.role == other.role and 
                self.num_false_responses == other.num_false_responses and 
                self.blacklisted == other.blacklisted)


class MCU:
    def __init__(self, name, line_names, manager, output_queue=None):
        self.name = name
        self.manager = manager
        self.interrupt_queue = Queue()
        self.interrupt_event = Event()
        
        
        self.stop_event = Event()
        self.output_queue = output_queue  # Queue to send data to main process
    
        
        self.all_lines = {ln: obj for ln, obj in line_names}
        
        self.pin_data = {name: PinData(name, line) for name, line in self.all_lines.items()}

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

    def _send_pin_data_to_main(self, pin_data, status):
        """Send pin data to main process via output queue"""
        if self.output_queue:
            data = {
                'mcu_name': self.name,
                'pin_data': pin_data.to_dict(),
                'status': status,
                'timestamp': perf_counter()
            }
            self.output_queue.put(data)

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
        print(f"Stopping MCU {self.name}...")
        self.stop_event.set()
        self.interrupt_event.set()

    def _run_logic(self):
        print(f"TEST: {self.name} starting logic with {len(self.all_lines)} lines")

        
        slot = None
        slot_start = None
        timeout = None
        responding_timeout = None
    

        while not all(self.pin_data[name].is_tested() for name in self.pin_data) and not self.stop_event.is_set():
            self._process_interrupts()
            state = self.state.value

            if state == INIT:
                available = [ln for ln in self.all_lines.keys() if not self.pin_data[ln].is_tested()]
                
                self.current_line = random.choice(available)
                slot = random.choice(TIME_SLOTS_MS)
                slot_start = perf_counter()
                responding_timeout = None
                print(f"[{self.name}] Time slot: {slot} ms for line {self.current_line}", flush=True)
                
                

                while (perf_counter() - slot_start) < slot / 1000.0 and self.state.value == INIT:
                    active_lines = [name for name, line in self.all_lines.items() if line.state() == 1]
                    
                    if active_lines and not self.pin_data[active_lines[0]].is_blacklisted():
                        #TODO: How to handle multiple active lines?
    
                        self.current_line = active_lines[0]
                        print(f"[{self.name}] Line active on {self.current_line}, entering MAYBE_RESPONDER state", flush=True)
                        self.state.value = MAYBE_RESPONDER
                        break
                    sleep(0.0001)

                if self.state.value != INIT:
                    print(f"[{self.name}] Interrupt processed, state is now {self.state.value}", flush=True)
                    continue
                
                self.set_curent_line.value = True
                print(f"[{self.name}] Send SYN on {self.current_line}", flush=True)
                self.pin_data[self.current_line].set_syn(True)
                 
                self.pin_data[self.current_line].set_role('initiator')
                self.current_line_obj.pull_high(self.name)
                
                syn_start = perf_counter()
                syn_end = syn_start + (SYN_DURATION / 1000.0)
                
                confict_detected = False
                
                while perf_counter() < syn_end:
                    other_active_lines = [name for name, line in self.all_lines.items() 
                                        if name != self.current_line and line.state() == 1]
                    
                    if other_active_lines and not self.pin_data[other_active_lines[0]].is_blacklisted():
                        self.current_line_obj.release(self.name)
                        self.set_curent_line.value = False
                        
                        self.current_line = other_active_lines[0]
                        print(f"[{self.name}] Conflict detected on {self.current_line}, switching to MAYBE_RESPONDER", flush=True)
                        self.state.value = MAYBE_RESPONDER
                        confict_detected = True
                        break
                        
                if not confict_detected:
                    self.last_sent_time.value = perf_counter()
                    self.current_line_obj.release(self.name)
                    print(f"[{self.name}] SYN sent on {self.current_line}", flush=True)
    
                    self.state.value = INITIATOR
                    self.role.value = 'initiator'
                    self.pin_data[self.current_line].set_role('initiator')
                
            elif state == MAYBE_RESPONDER:
                responding_timeout = perf_counter() + TIMEOUT_RESPONDER
                has_seen_signal = False

                while perf_counter() < responding_timeout:
                    self._process_interrupts()

            
                    if self.received_syn.value and self.received_syn_on.value == self.current_line:
                        print(f"[{self.name}] SYN received on {self.current_line}, switching to RESPONDER", flush=True)
                        self.received_syn.value = False
                        self.state.value = RESPONDER
                        self.role.value = 'responder'
                        self.pin_data[self.current_line].set_role('responder')
                        break
                else:
                    print(f"[{self.name}] Timeout waiting for SYN on {self.current_line}, returning to INIT", flush=True)
                    self.pin_data[self.current_line].increment_false_responses()
                    self._reset_state()
                        
            elif state == INITIATOR:
                timeout = perf_counter() + TIMEOUT_SYN_ACK
                print(f"[{self.name}] Waiting for SYN_ACK on {self.current_line}", flush=True)
                has_seen_signal = False

                while perf_counter() < timeout:
                    self._process_interrupts()
                    
                    other_active_lines = [name for name, line in self.all_lines.items() 
                                        if name != self.current_line and line.state() == 1]
                    if other_active_lines and not self.pin_data[other_active_lines[0]].is_blacklisted():
                        self.current_line = other_active_lines[0]
                        print(f"[{self.name}] Other line {self.current_line} is high, switching to MAYBE_RESPONDER", flush=True)
                        self.state.value = MAYBE_RESPONDER
                        break
                    
                    if self.current_line and self.current_line_obj and self.current_line_obj.state() == 1:
                        if not has_seen_signal:
                            print(f"[{self.name}] Line {self.current_line} is high, waiting for SYN_ACK", flush=True)
                            timeout = perf_counter() + (SYN_ACK_DURATION + TOLERANCE) / 1000.0
                            has_seen_signal = True
                    
                    if self.received_syn_ack.value and self.received_syn_ack_on.value == self.current_line:
                        print(f"[{self.name}] SYN_ACK received on {self.current_line}", flush=True)
                        self.pin_data[self.current_line].set_syn_ack(True)
                        self.received_syn_ack.value = False
                        self.received_syn_ack_on.value = ''
                        
                        sleep(LINE_SETTLE_DURATION / 1000.0)
                        
                        self.set_curent_line.value = True
                        self.current_line_obj.pull_high(self.name)
                        sleep(ACK_DURATION / 1000.0)
                        self.pin_data[self.current_line].set_ack(True)
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
                self.pin_data[self.current_line].set_syn_ack(True)
                self.last_sent_time.value = perf_counter()
                self.current_line_obj.release(self.name)
                self.set_curent_line.value = False
                print(f"[{self.name}] Send SYN_ACK on {self.current_line}", flush=True)
                
                sleep(LINE_SETTLE_DURATION / 1000.0)

                responding_timeout = perf_counter() + TIMEOUT_ACK
                
                print(f"[{self.name}] Waiting for ACK on {self.current_line}", flush=True)
                has_seen_signal = False

                while perf_counter() < responding_timeout:
                    self._process_interrupts()
                    
                    if self.current_line and self.current_line_obj and self.current_line_obj.state() == 1:
                        if not has_seen_signal:
                            print(f"[{self.name}] Line {self.current_line} is high, waiting for ACK", flush=True)
                            responding_timeout = perf_counter() + (ACK_DURATION + TOLERANCE) / 1000.0
                            has_seen_signal = True
                            
                    if self.received_ack.value and self.received_ack_on.value == self.current_line:
                        self.pin_data[self.current_line].set_ack(True)
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
                self.pin_data[self.current_line].set_role(self.role.value)
                self.pin_data[self.current_line].set_successful(True)
                
                self._send_pin_data_to_main(self.pin_data[self.current_line], 'WORKING')
                self._reset_state()

            elif state == FAILED:
                print(f"[{self.name}] ❌ {self.current_line} failed as {self.role.value}", flush=True)
                self.pin_data[self.current_line].set_blacklisted()
                
                self._send_pin_data_to_main(self.pin_data[self.current_line], 'FAILED')
                
                self._reset_state()
        if self.output_queue:
            if all(pd.is_tested() for pd in self.pin_data.values()):
                self.output_queue.put({
                    'mcu_name': self.name,
                    'status': 'COMPLETED',
                    'timestamp': perf_counter(),
                    'white_list': [pd.to_dict() for pd in self.pin_data.values() if pd.role == 'initiator' and pd.successful],
                    'black_list': [pd.to_dict() for pd in self.pin_data.values() if pd.is_blacklisted()]
                })

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
