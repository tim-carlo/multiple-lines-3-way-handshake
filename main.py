from multiprocessing import Queue, Manager, set_start_method
from time import perf_counter
from shared_lines import SharedLine, OneWaySharedLine, UnreliableSharedLine, MultiLinePlotter
from mcu import MCU
from pinger import Pinger, Bridge


from hypothesis import given, strategies as st

if __name__ == "__main__":
    set_start_method("fork")
    manager = Manager()
    
    # Create output queue for collecting MCU data
    output_queue = Queue()
    
    # Create shared lines
    shared_lines = {
        "L1": SharedLine(manager),
        "L2": SharedLine(manager), 
        "L3": SharedLine(manager),
        "L4": SharedLine(manager),
    }
    
    # Scenario 4: Asymmetric connection (one controller has more lines)
    lines_controller1 = [
      ("L1", shared_lines["L1"]),
      ("L2", shared_lines["L2"]),
      ("L3", shared_lines["L3"]),
    ]
    
    lines_controller2 = [
      ("L1", shared_lines["L1"]),
      ("L2", shared_lines["L2"]),
      ("L3", shared_lines["L3"]),
    ]
    
    #TODO: Enhance the algorithm to react to periodic pings on one line
    
    mcu1 = MCU("A", lines_controller1, manager, output_queue)
    mcu2 = MCU("B", lines_controller2, manager, output_queue)
    
     #start the pinger
   # pinger1 = Pinger(shared_lines["L2"], interval=1.0, pulse_width=0.1)
   # pinger2 = Pinger(shared_lines["L10"], interval=1.0, pulse_width=0.1)
    
    

    mcu1.start()
    mcu2.start()
    
  #  pinger1.start()
  #  pinger2.start()
    
    
    
     
    
    # Collect pin data from MCUs
    mcu_results = {}
    completed_mcus = set()
    
    try:
        start_time = perf_counter()
        while len(completed_mcus) < 2 and (perf_counter() - start_time) < 25:
            try:
                # Check for data from MCUs with timeout
                data = output_queue.get(timeout=1.0)
                mcu_name = data['mcu_name']
                
                if mcu_name not in mcu_results:
                    mcu_results[mcu_name] = {'pins': [], 'status': 'RUNNING'}
                
                if data['status'] == 'COMPLETED':
                    completed_mcus.add(mcu_name)
                    mcu_results[mcu_name]['status'] = 'COMPLETED'
                    mcu_results[mcu_name]['white_list'] = data['white_list']
                    mcu_results[mcu_name]['black_list'] = data['black_list']
                elif 'pin_data' in data:
                    mcu_results[mcu_name]['pins'].append(data)
                    
            except:
                # Timeout or empty queue, continue
                continue
                
    finally:
        mcu1.stop()
        mcu2.stop()
        mcu1.join()
        mcu2.join()
    #    pinger1.stop()
    #    pinger2.stop()
    #    pinger1.join()
    #    pinger2.join()
    
    # Print final summary
    
    for mcu_name, results in mcu_results.items():
        print(f"\nMCU {mcu_name}:")
        print(f"Status: {results['status']}")
        if 'white_list' in results:
            print(f"Working pins: {results['white_list']}")
            print(f"Failed pins: {results['black_list']}")
        print(f"Pin test details ({len(results['pins'])} tests):")
        for pin_result in results['pins']:
            pd = pin_result['pin_data']
            print(f"  {pd['name']}: {pin_result['status']} as {pd['role']}")
            
    
    
    # Log end state for all lines
    for line in shared_lines.values():
        line.log_end()
        
    # Plotting
    plotter = MultiLinePlotter([])
    
    # Add all lines to the plotter
    for name, line in shared_lines.items():
        plotter.add_line(line)
    
    plotter.plot_all()
