"""Thread-safe container implementations.

Provides lightweight thread-safe wrappers around dict, list, and set
behaviors used elsewhere in the project. These wrappers use reentrant locks
to ensure safe concurrent access and support context-manager locking for
batch operations.
"""

from threading import RLock
from collections import UserDict, UserList


class ThreadSafeDict(UserDict):
    """A dict-like mapping with thread-safe operations using a reentrant lock.

    Most mapping methods acquire the lock before delegating to the base
    implementation. Use the object as a context manager to acquire the lock
    manually for multi-step atomic operations::

        with tsd:
            # perform multiple reads/writes
    """

    def __init__(self, *args, **kwargs):
        """Initialize the mapping and the reentrant lock."""
        super().__init__(*args, **kwargs)
        self._lock = RLock()  # Use RLock for reentrant locking
   
    def get(self, key, default=None):
        with self._lock:
            return super().get(key, default)

    def pop(self, key, default=None):
        with self._lock:
            return super().pop(key, default)
    # pylint: disable=W0221
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

    def unsafe_len(self):
        """Return the length without acquiring the lock (fast, potentially racy)."""
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
    """A list-like container with thread-safe operations using a reentrant lock.

    Common mutation and access methods acquire the lock before delegating to
    the underlying `UserList` implementation. Use as a context manager to
    perform multiple operations atomically::

        with tsl:
            # multiple appends/pops
    """

    def __init__(self, *args):
        """Initialize the list and its reentrant lock."""
        super().__init__(*args)
        self._lock = RLock()  # Use RLock for reentrant locking

    def append(self, item):
        """Append `item` to the list (thread-safe)."""
        with self._lock:
            super().append(item)

    def extend(self, other):
        """Extend list by appending elements from the iterable `other` (thread-safe)."""
        with self._lock:
            super().extend(other)

    def insert(self, i, item):
        """Insert `item` before position `i` (thread-safe)."""
        with self._lock:
            super().insert(i, item)

    def pop(self, i=-1):
        """Remove and return item at index `i` (default last) (thread-safe)."""
        with self._lock:
            return super().pop(i)

    def remove(self, item):
        """Remove first occurrence of `item` (thread-safe)."""
        with self._lock:
            super().remove(item)

    def __setitem__(self, index, value):
        """Set `self[index] = value` (thread-safe)."""
        with self._lock:
            super().__setitem__(index, value)

    def __delitem__(self, index):
        """Delete item at `index` (thread-safe)."""
        with self._lock:
            super().__delitem__(index)

    def __getitem__(self, index):
        """Return the item at `index` (thread-safe)."""
        with self._lock:
            return super().__getitem__(index)

    def __iter__(self):
        """Return an iterator over a snapshot of the list (does not hold the lock)."""
        with self._lock:
            # Returns a snapshot so the loop can run without holding the lock
            return iter(list(self.data))

    def __len__(self):
        """Return the number of items in the list (thread-safe)."""
        with self._lock:
            return super().__len__()

    def unsafe_len(self):
        """Return the length without acquiring the lock (fast, potentially racy)."""
        return super().__len__()

    def __enter__(self):
        """Acquire the underlying lock and return self for use as a context manager."""
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Release the underlying lock when exiting the context manager."""
        self._lock.release()

    def __repr__(self):
        """Return a thread-safe string representation of the list."""
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

    def unsafe_len(self):
        """Return the number of items in the set without locking."""
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

    def __repr__(self):
        """Return a thread-safe string representation of the set."""
        with self._lock:
            return f"ThreadSafeSet({self._set!r})"