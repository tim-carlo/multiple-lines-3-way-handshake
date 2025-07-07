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

DEAD_LINE_PINS = 


class PinData:
    def __init__(self):
        self.ack = False
        self.syn_ack = False
        self.ack = False
        
        self.last_signal_time = 0.0
        
    def set_ack(self, ack):
        self.ack = ack
        self.last_signal_time = perf_counter()
    def set_syn_ack(self, syn_ack):
        self.syn_ack = syn_ack
        self.last_signal_time = perf_counter()
    def set_ack(self, ack):
        self.ack = ack
        self.last_signal_time = perf_counter()
    def is_dead(self):
        return perf_counter() - self.last_signal_time > PASSIVE_RESPONDER_TIMEOUT
    


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
        
        
        self.last_line = manager.Value('u', '')
        self.testedPins = manager.dict()
        

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
        random_release_point = 0.0
        
        while True:
            self._process_interrupts()
            
            
    
        
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
        "L8": SharedLine(manager),
        "L9": SharedLine(manager),
        "L6": SharedLine(manager),  # Unreliable line
        "L7": OneWaySharedLine(manager, sender_name="A")  # One
    }
    
    # Define lines for each controller
    lines_controller1 = [
        ("L1", shared_lines["L1"]),
        ("L2", shared_lines["L2"]),
        ("L3", shared_lines["L3"]),
        ("L6", shared_lines["L6"]),
        ("L9", shared_lines["L9"]),
        ("L5", shared_lines["L5"]),  # Add L5 to controller 1
        ("L8", shared_lines["L8"]),  # Add L8 to controller 1
    ]
    
    lines_controller2 = [
        ("L1", shared_lines["L1"]),
        ("L2", shared_lines["L2"]),
        ("L3", shared_lines["L3"]),
        ("L4", shared_lines["L4"]),
        ("L6", shared_lines["L6"]),
        ("L9", shared_lines["L9"]),
        ("L8", shared_lines["L8"]),  # Add L8 to controller 2
    ]
    
    mcu1 = MCU("A", lines_controller1, manager)
    mcu2 = MCU("B", lines_controller2, manager)

    mcu1.start()
    mcu2.start()

    try:
        sleep(15)
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
    