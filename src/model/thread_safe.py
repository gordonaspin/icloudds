"""Thread-safe container implementations.

Provides lightweight thread-safe wrappers around dict, list, and set
behaviors used elsewhere in the project. These wrappers use reentrant locks
to ensure safe concurrent access and support context-manager locking for
batch operations.
"""
from threading import RLock
from pathlib import Path
from collections import UserDict, UserList
from typing import Union, Any, Iterator, Tuple, Iterable, Set

class ThreadSafePathDict(UserDict):
    """
    A thread-safe dict using RLock that accepts str or Path objects 
    as indices by normalizing them to a standard string representation.
    """

    def __init__(self, *args, **kwargs):
        self._lock = RLock()
        super().__init__(*args, **kwargs)

    def _normalize(self, key: Union[str, Path]) -> str:
        return str(Path(key))

    # accessor methods
    def get(self, key, default=None):
        with self._lock:
            return super().get(self._normalize(key), default)

    def pop(self, key, default=None):
        with self._lock:
            return super().pop(self._normalize(key), default)

    def update(self, other=None, **kwargs):  # pylint: disable=arguments-differ
        """Update mapping with another mapping or iterable and/or keyword args.
        Uses the same signature as the built-in `dict.update(other=None, **kwargs)`
        to avoid Pylint W0221 (arguments-differ) when overriding.
        """
        with self._lock:
            if other is None:
                # Only keyword args provided
                super().update(**kwargs)
            else:
                super().update(other, **kwargs)

    def clear(self):
        with self._lock:
            super().clear()

    # --- Context Manager Methods ---
    def __enter__(self):
        """Allows 'with d:' to hold a lock for atomic multi-step operations."""
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._lock.release()

    # --- Thread-Safe Accessors ---
    def __getitem__(self, key: Union[str, Path]) -> Any:
        with self._lock:
            return super().__getitem__(self._normalize(key))

    def __setitem__(self, key: Union[str, Path], value: Any) -> None:
        with self._lock:
            super().__setitem__(self._normalize(key), value)

    def __delitem__(self, key: Union[str, Path]) -> None:
        with self._lock:
            super().__delitem__(self._normalize(key))

    def __contains__(self, key):
        with self._lock:
            return super().__contains__(self._normalize(key))

    def __len__(self):
        with self._lock:
            return super().__len__()

    def unsafe_len(self):
        """Return the length without acquiring the lock (fast, potentially racy)."""
        return super().__len__()

    # --- Iterator Methods (Snapshotting) ---
    def __iter__(self) -> Iterator[str]:
        """Iterates over a copy of keys to remain thread-safe."""
        with self._lock:
            return iter(list(self.data.keys()))

    def keys(self):
        with self._lock:
            # Returns a snapshot set to support set operations
            return set(self.data.keys())

    def values(self) -> Iterator[Any]:
        with self._lock:
            return iter(list(self.data.values()))

    def items(self) -> Iterator[Tuple[str, Any]]:
        with self._lock:
            return iter(list(self.data.items()))

     # --- Set Operation Support ---
    def __or__(self, other: Union[dict, 'ThreadSafePathDict']) -> 'ThreadSafePathDict':
        """Union: Returns a NEW dictionary containing keys from both."""
        with self._lock:
            # Create a copy and update it with the other mapping
            new_dict = self.__class__(self.data)
            new_dict.update(other)
            return new_dict

    def __and__(self, other: Iterable[Any]) -> Set[str]:
        """Intersection: Returns a set of keys present in both."""
        # Normalize the other keys first
        other_keys = {self._normalize(k) for k in other}
        with self._lock:
            return set(self.data.keys()) & other_keys

    def __sub__(self, other: Iterable[Any]) -> Set[str]:
        """Difference: Returns a set of keys in self but NOT in other."""
        other_keys = {self._normalize(k) for k in other}
        with self._lock:
            return set(self.data.keys()) - other_keys

    def __xor__(self, other: Iterable[Any]) -> Set[str]:
        """Symmetric Difference: Keys in either self or other, but not both."""
        other_keys = {self._normalize(k) for k in other}
        with self._lock:
            return set(self.data.keys()) ^ other_keys

    def __repr__(self):
        with self._lock:
            return f"ThreadSafeDict({super().__repr__()})"


class ThreadSafePathList(UserList):
    """
    A thread-safe list using RLock that accepts str or Path objects 
    as indices by normalizing them to a standard string representation.
    """
    def __init__(self, *args, **kwargs):
        self._lock = RLock()
        super().__init__(*args, **kwargs)

    def _normalize(self, value: Any) -> Any:
        """Helper to ensure paths are stored consistently as strings."""
        if isinstance(value, (str, Path)):
            return str(Path(value))
        return value

    # --- Core Indexing Methods ---
    def __getitem__(self, index: Union[int, str, Path, slice]) -> Any:
        with self._lock:
            if isinstance(index, (str, Path)):
                target = self._normalize(index)
                try:
                    return self.data[self.data.index(target)]
                except ValueError as e:
                    raise KeyError(f"Path {index} not found in list.") from e
            return super().__getitem__(index)

    def __setitem__(self, index: int, item: Any) -> None:
        """Normalizes the item before setting it at the specified index."""
        with self._lock:
            super().__setitem__(index, self._normalize(item))

    def __delitem__(self, index: Union[int, str, Path, slice]) -> None:
        """Deletes item by integer index or by path search."""
        with self._lock:
            if isinstance(index, (str, Path)):
                target = self._normalize(index)
                try:
                    self.data.remove(target)
                except ValueError as e:
                    raise KeyError(f"Path {index} not found in list.") from e
            else:
                super().__delitem__(index)

    # --- Mutators ---
    def append(self, item: Any) -> None:
        with self._lock:
            self.data.append(self._normalize(item))

    def extend(self, other: Iterable[Any]) -> None:
        normalized = [self._normalize(i) for i in other]
        with self._lock:
            self.data.extend(normalized)

    def insert(self, i: int, item: Any) -> None:
        """Insert normalized item at index i."""
        with self._lock:
            self.data.insert(i, self._normalize(item))

    def pop(self, i: int = -1) -> Any:
        """Remove and return item at index i (default last)."""
        with self._lock:
            return self.data.pop(i)

    def remove(self, item: Any) -> None:
        """Remove the first occurrence of the (normalized) item."""
        target = self._normalize(item)
        with self._lock:
            self.data.remove(target)

    # --- Context Manager ---
    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *args):
        self._lock.release()

    # --- Iteration ---
    def __iter__(self):
        """Return an iterator over a snapshot of the list (does not hold the lock)."""
        with self._lock:
            return iter(list(self.data))

    def __len__(self):
        """Return the number of items in the list (thread-safe)."""
        with self._lock:
            return super().__len__()

    def unsafe_len(self):
        """Return the length without acquiring the lock (fast, potentially racy)."""
        return super().__len__()

    def __contains__(self, item):
        """Check if an item is in the list (thread-safe)."""
        with self._lock:
            return item in self.data

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

    def update(self, *others):
        """Update the set with one or more iterables in a thread-safe manner."""
        with self._lock:
            self._set.update(*others)

    def __contains__(self, item):
        """Check if an item is in the set (thread-safe)."""
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
            # Returns a snapshot so the loop can run without holding the lock
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
