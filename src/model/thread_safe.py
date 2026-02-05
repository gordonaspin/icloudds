from threading import RLock
from collections import UserDict, UserList

class ThreadSafeDict(UserDict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lock = RLock() # Use RLock for reentrant locking
            
    def get(self, key, default=None):
        with self._lock:
            return super().get(key, default)
            
    def pop(self, key, default=None):
        with self._lock:
            return super().pop(key, default)
            
    def update(self, *args, **kwargs):
        with self._lock:
            super().update(*args, **kwargs)

    def clear(self):
        with self._lock:
            super().clear()

    def keys(self):
        with self._lock:
            return super().keys()
            
    def values(self):
        with self._lock:
            return super().values()
            
    def items(self):
        with self._lock:
            return super().items()

    def __setitem__(self, key, value):
        with self._lock:
            super().__setitem__(key, value)

    def __getitem__(self, key):
        with self._lock:
            return super().__getitem__(key)

    def __delitem__(self, key):
        with self._lock:
            super().__delitem__(key)
            
    def __contains__(self, key):
        with self._lock:
            return super().__contains__(key)
            
    def __iter__(self):
        # Iteration should be done on a consistent snapshot of the data
        with self._lock:
            return iter(list(self.data.keys()))

    def __len__(self):
        with self._lock:
            return super().__len__()
        
    def unstable_len(self):
        return super().__len__()

    # Ensure context manager for the lock itself
    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()

    def __repr__(self):
        with self._lock:
            return f"ThreadSafeDict({super().__repr__()})"
        

class ThreadSafeList(UserList):
    def __init__(self, *args):
        super().__init__(*args)
        self._lock = RLock() # Use RLock for reentrant locking

    def append(self, value):
        with self._lock:
            super().append(value)

    def extend(self, other):
        with self._lock:
            super().extend(other)

    def insert(self, i, item):
        with self._lock:
            super().insert(i, item)

    def pop(self, i=-1):
        with self._lock:
            return super().pop(i)

    def remove(self, item):
        with self._lock:
            super().remove(item)
        
    def __setitem__(self, index, value):
        with self._lock:
            super().__setitem__(index, value)

    def __delitem__(self, index):
        with self._lock:
            super().__delitem__(index)

    def __getitem__(self, index):
        with self._lock:
            return super().__getitem__(index)
        
    def __iter__(self):
        with self._lock:
        # Returns a snapshot so the loop can run without holding the lock
            return iter(list(self.data))
        
    def __len__(self):
        with self._lock:
            return super().__len__()

    def unstable_len(self):
        return super().__len__()

    def __enter__(self):
        """Enables context manager support for manual locking blocks."""
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Releases the lock after the context block finishes."""
        self._lock.release()

    def __repr__(self):
        with self._lock:
            return f"ThreadSafeList({super().__repr__()})"

class ThreadSafeSet:
    """A thread-safe set implementation using a threading.Lock."""
    def __init__(self, initial_data=None):
        self._set = set(initial_data if initial_data is not None else [])
        self._lock = RLock()

    def add(self, item):
        """Add an item to the set in a thread-safe manner."""
        with self._lock:
            self._set.add(item)

    def remove(self, item):
        """Remove an item from the set in a thread-safe manner."""
        with self._lock:
            self._set.remove(item)

    def update(self, items):
        """Update the set with multiple items in a thread-safe manner."""
        with self._lock:
            self._set.update(items)

    def contains(self, item):
        """Check if an item is in the set in a thread-safe manner."""
        with self._lock:
            return item in self._set

    def clear(self):
        """Remove all items from the set in a thread-safe manner."""
        with self._lock:
            self._set.clear()

    def __len__(self):
        """Return the number of items in the set."""
        with self._lock:
            return len(self._set)
        
    def unstable_len(self):
        return len(self._set)
            
    def __iter__(self):
        """Return a thread-safe iterator over a copy of the set."""
        with self._lock:
            # Iterate over a copy to prevent issues if the set is modified 
            # by another thread during iteration.
            return iter(set(self._set))
        
    def __enter__(self):
        """Enables context manager support for manual locking blocks."""
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Releases the lock after the context block finishes."""
        self._lock.release()