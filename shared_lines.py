import random

class SharedLine:
    def __init__(self, manager):
        self.holders = manager.list()

    def pull_high(self, name):
        if name not in self.holders:
            self.holders.append(name)

    def release(self, name):
        if name in self.holders:
            self.holders.remove(name)

    def state(self):
        return 1 if len(self.holders) > 0 else 0
    
class OneWaySharedLine:
    def __init__(self, manager, sender_name):
        self._value = manager.Value('i', 0)
        self._sender_name = sender_name

    def pull_high(self, name):
        if name == self._sender_name:
            self._value.value = 1

    def release(self, name):
        if name == self._sender_name:
            self._value.value = 0

    def state(self):
        return self._value.value
    

class UnreliableSharedLine:
    def __init__(self, manager, failure_rate=0.1):
        self.holders = manager.list()
        self.failure_rate = failure_rate  # z. B. 0.1 = 10% Ausfallrate

    def pull_high(self, name):
        if name not in self.holders:
            self.holders.append(name)

    def release(self, name):
        if name in self.holders:
            self.holders.remove(name)
            

    def state(self):
       # Simmulate an unreliable line by randomly failing to report the state
        if random.random() < self.failure_rate:
            return 0  # Leitung „scheint“ low, obwohl sie gezogen wird
        return 1 if len(self.holders) > 0 else 0