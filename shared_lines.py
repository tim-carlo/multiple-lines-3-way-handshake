import random
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

class SharedLine:
    def __init__(self, manager, name="SharedLine"):
        self.holders = manager.list()
        self.data_log = manager.list()
        self.name = name

    def pull_high(self, name):
        if name not in self.holders:
            self.holders.append(name)
        self._log_state()

    def release(self, name):
        if name in self.holders:
            self.holders.remove(name)
        self._log_state()

    def state(self):
        return 1 if len(self.holders) > 0 else 0

    def _log_state(self):
        self.data_log.append({
            'timestamp': datetime.now(),
            'state': self.state(),
            'holders_count': len(self.holders)
        })

    def get_dataframe(self):
        return pd.DataFrame(list(self.data_log))


class OneWaySharedLine:
    def __init__(self, manager, sender_name, name="OneWaySharedLine"):
        self._value = manager.Value('i', 0)
        self._sender_name = sender_name
        self.data_log = manager.list()
        self.name = name

    def pull_high(self, name):
        if name == self._sender_name:
            self._value.value = 1
        self._log_state()

    def release(self, name):
        if name == self._sender_name:
            self._value.value = 0
        self._log_state()

    def state(self):
        return self._value.value

    def _log_state(self):
        self.data_log.append({
            'timestamp': datetime.now(),
            'state': self.state(),
            'sender': self._sender_name
        })

    def get_dataframe(self):
        return pd.DataFrame(list(self.data_log))


class UnreliableSharedLine:
    def __init__(self, manager, failure_rate=0.1, name="UnreliableSharedLine"):
        self.holders = manager.list()
        self.failure_rate = failure_rate
        self.data_log = manager.list()
        self.name = name

    def pull_high(self, name):
        if name not in self.holders:
            self.holders.append(name)
        self._log_state()

    def release(self, name):
        if name in self.holders:
            self.holders.remove(name)
        self._log_state()

    def state(self):
        if random.random() < self.failure_rate:
            return 0
        return 1 if len(self.holders) > 0 else 0

    def _log_state(self):
        actual_state = 1 if len(self.holders) > 0 else 0
        reported_state = 0 if random.random() < self.failure_rate else actual_state
        self.data_log.append({
            'timestamp': datetime.now(),
            'actual_state': actual_state,
            'reported_state': reported_state,
            'failed': actual_state != reported_state,
            'holders_count': len(self.holders)
        })

    def get_dataframe(self):
        return pd.DataFrame(list(self.data_log))
    
class MultiLinePlotter:
    def __init__(self, lines=None):
        self.lines = lines or []
    
    def add_line(self, line):
        self.lines.append(line)
    
    def add_lines(self, lines):
        self.lines.extend(lines)
    
    def plot_all(self, figsize=(15, 10)):
        if not self.lines:
            print("No lines to plot")
            return
        
        num_lines = len(self.lines)
        rows = (num_lines + 1) // 2  # Calculate rows needed for 2 columns
        
        fig, axes = plt.subplots(rows, 2, figsize=figsize)
        fig.suptitle('All Shared Lines State Overview', fontsize=16)
        
        # Handle single row case and ensure axes is always 2D
        if rows == 1 and num_lines == 1:
            # Special case: only one subplot needed
            axes = [[axes]]
        elif rows == 1:
            axes = axes.reshape(1, -1)
        
        for i, line in enumerate(self.lines):
            row = i // 2
            col = i % 2
            ax = axes[row][col]
            
            if not line.data_log:
                ax.text(0.5, 0.5, 'No data', ha='center', va='center', transform=ax.transAxes)
                ax.set_title(f'{line.name} - No Data')
                continue
            
            df = line.get_dataframe()
            # Handle different line types
            if isinstance(line, UnreliableSharedLine):
                ax.plot(df['timestamp'], df['actual_state'], label='Actual', alpha=0.7)
                ax.plot(df['timestamp'], df['reported_state'], label='Reported', alpha=0.7)
                # Mark failures
                failures = df[df['failed'] == True]
                if not failures.empty:
                    ax.scatter(failures['timestamp'], failures['reported_state'], 
                             color='red', s=20, label='Failures', zorder=5)
                ax.legend()
            else:
                state_col = 'state' if 'state' in df.columns else 'reported_state'
                ax.plot(df['timestamp'], df[state_col], alpha=0.7)
            
            ax.set_title(line.name)
            ax.set_xlabel('Time')
            ax.set_ylabel('State')
            ax.set_ylim(-0.1, 1.1)
        
        # Hide empty subplots if odd number of lines
        if num_lines % 2 == 1 and num_lines > 1:
            axes[rows-1][1].set_visible(False)
        
        plt.tight_layout()
        plt.show()