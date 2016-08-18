"Fanout cache automatically shards keys and values."

import os.path as op
import time

from .core import Cache, Disk, ENOVAL, Timeout


class FanoutCache(object):
    "Cache that shards keys and values."
    def __init__(self, directory, shards=8, timeout=0.025, disk=Disk,
                 **settings):
        """Initialize cache instance.

        :param str directory: cache directory
        :param int shards: number of shards to distribute writes
        :param float timeout: SQLite connection timeout
        :param disk: `Disk` instance for serialization
        :param settings: any of `DEFAULT_SETTINGS`

        """
        self._count = shards
        self._shards = tuple(
            Cache(
                op.join(directory, '%03d' % num),
                timeout=timeout,
                disk=disk,
                **settings
            )
            for num in range(shards)
        )


    def __getattr__(self, name):
        return getattr(self._shards[0], name)


    def set(self, key, value, expire=None, read=False, tag=None, retry=False):
        """Set `key` and `value` item in cache.

        When `read` is `True`, `value` should be a file-like object opened
        for reading in binary mode.

        :param key: key for item
        :param value: value for item
        :param float expire: seconds until the key expires
            (default None, no expiry)
        :param bool read: read value as raw bytes from file (default False)
        :param str tag: text to associate with key (default None)
        :param bool retry: retry if database timeout expires (default False)
        :return: True if item is set

        """
        index = hash(key) % self._count
        set_func = self._shards[index].set

        while True:
            try:
                return set_func(key, value, expire, read, tag)
            except Timeout:
                if retry:
                    continue
                else:
                    return False


    def __setitem__(self, key, value):
        """Set `key` and `value` item in cache.

        :param key: key for item
        :param value: value for item

        """
        index = hash(key) % self._count
        set_func = self._shards[index].set

        while True:
            try:
                return set_func(key, value)
            except Timeout:
                continue


    def add(self, key, value, expire=None, read=False, tag=None, retry=False):
        """Add `key` and `value` item to cache.

        Similar to `set`, but only add to cache if key not present.

        This operation is atomic. Only one concurrent add operation for given
        key from separate threads or processes will succeed.

        When `read` is `True`, `value` should be a file-like object opened
        for reading in binary mode.

        :param key: key for item
        :param value: value for item
        :param float expire: seconds until the key expires
            (default None, no expiry)
        :param bool read: read value as bytes from file (default False)
        :param str tag: text to associate with key (default None)
        :param bool retry: retry if database timeout expires (default False)
        :return: True if item is added

        """
        index = hash(key) % self._count
        add_func = self._shards[index].add

        while True:
            try:
                return add_func(key, value, expire, read, tag)
            except Timeout:
                if retry:
                    continue
                else:
                    return False


    def get(self, key, default=None, read=False, expire_time=False, tag=False,
            retry=False):
        """Retrieve value from cache. If `key` is missing, return `default`.

        :param key: key for item
        :param default: return value if key is missing (default None)
        :param bool read: if True, return file handle to value
            (default False)
        :param float expire_time: if True, return expire_time in tuple
            (default False)
        :param tag: if True, return tag in tuple (default False)
        :param bool retry: retry if database timeout expires (default False)
        :return: value for item if key is found else default

        """
        index = hash(key) % self._count
        get_func = self._shards[index].get

        while True:
            try:
                return get_func(
                    key, default=default, read=read, expire_time=expire_time,
                    tag=tag,
                )
            except Timeout:
                if retry:
                    continue
                else:
                    return default


    def __getitem__(self, key):
        """Return corresponding value for `key` from cache.

        :param key: key for item
        :return: value for item
        :raises KeyError: if key is not found

        """
        value = self.get(key, default=ENOVAL)
        if value is ENOVAL:
            raise KeyError(key)
        return value


    def read(self, key):
        """Return file handle corresponding to `key` from cache.

        :param key: key for item
        :return: file open for reading in binary mode
        :raises KeyError: if key is not found

        """
        handle = self.get(key, default=ENOVAL, read=True, retry=True)
        if handle is ENOVAL:
            raise KeyError(key)
        return handle


    def __contains__(self, key):
        """Return `True` if `key` matching item is found in cache.

        :param key: key for item
        :return: True if key is found

        """
        index = hash(key) % self._count
        return key in self._shards[index]


    def delete(self, key, retry=False):
        """Delete corresponding item for `key` from cache.

        Missing keys are ignored.

        :param key: key for item
        :param bool retry: retry if database timeout expires (default False)
        :return: True if item is deleted

        """
        index = hash(key) % self._count
        del_func = self._shards[index].__delitem__

        while True:
            try:
                return del_func(key)
            except Timeout:
                if retry:
                    continue
                else:
                    return False
            except KeyError:
                return False


    def __delitem__(self, key):
        """Delete corresponding item for `key` from cache.

        :param key: key for item
        :raises KeyError: if key is not found

        """
        index = hash(key) % self._count
        del_func = self._shards[index].__delitem__

        while True:
            try:
                return del_func(key)
            except Timeout:
                continue


    def check(self, fix=False):
        """Check database and file system consistency.

        Intended for use in testing and post-mortem error analysis.

        While checking the cache table for consistency, a writer lock is held
        on the database. The lock blocks other cache clients from writing to
        the database. For caches with many file references, the lock may be
        held for a long time. For example, local benchmarking shows that a
        cache with 1,000 file references takes ~60ms to check.

        :param bool fix: correct inconsistencies
        :return: list of warnings
        :raises Timeout: if database timeout expires

        """
        return sum((shard.check(fix=fix) for shard in self._shards), [])


    def expire(self):
        """Remove expired items from cache.

        :return: count of items removed

        """
        return self._remove('expire', (time.time(),))


    def create_tag_index(self):
        """Create tag index on cache database.

        It's better to initialized cache with `tag_index=True`.

        :raises Timeout: if database timeout expires

        """
        for shard in self._shards:
            shard.create_tag_index()


    def drop_tag_index(self):
        """Drop tag index on cache database.

        :raises Timeout: if database timeout expires

        """
        for shard in self._shards:
            shard.drop_tag_index()


    def evict(self, tag):
        """Remove items with matching `tag` from cache.

        :param str tag: tag identifying items
        :return: count of items removed

        """
        return self._remove('evict', (tag,))


    def clear(self):
        """Remove all items from cache.

        :return: count of items removed

        """
        return self._remove('clear')


    def _remove(self, name, args=()):
        total = 0
        for shard in self._shards:
            method = getattr(shard, name)
            while True:
                try:
                    count = method(*args)
                    total += count
                except Timeout as timeout:
                    total += timeout.args[0]
                else:
                    if not count:
                        break
        return total


    def stats(self, enable=True, reset=False):
        """Return cache statistics hits and misses.

        :param bool enable: enable collecting statistics (default True)
        :param bool reset: reset hits and misses to 0 (default False)
        :return: (hits, misses)

        """
        results = [shard.stats(enable, reset) for shard in self._shards]
        return (sum(result[0] for result in results),
                sum(result[1] for result in results))


    def volume(self):
        """Return estimated total size of cache on disk.

        :return: size in bytes

        """
        return sum(shard.volume() for shard in self._shards)


    def close(self):
        "Close database connection."
        for shard in self._shards:
            shard.close()


    def __enter__(self):
        return self


    def __exit__(self, *exception):
        self.close()


    def __len__(self):
        return sum(len(shard) for shard in self._shards)


    def reset(self, key, value=ENOVAL):
        """Reset `key` and `value` item from Settings table.

        If `value` is not given, it is reloaded from the Settings
        table. Otherwise, the Settings table is updated.

        Settings attributes on cache objects are lazy-loaded and
        read-only. Use `reset` to update the value.

        Settings with the ``sqlite_`` prefix correspond to SQLite
        pragmas. Updating the value will execute the corresponding PRAGMA
        statement.

        :param str key: Settings key for item
        :param value: value for item (optional)
        :return: updated value for item
        :raises Timeout: if database timeout expires

        """
        for shard in self._shards:
            while True:
                try:
                    result = shard.reset(key, value)
                except Timeout:
                    pass
                else:
                    break
        return result
