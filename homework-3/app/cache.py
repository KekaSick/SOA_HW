"""Cache-Aside для карточки рейса и поиска."""
import logging
import os

from redis.exceptions import RedisError
from redis.sentinel import Sentinel

log = logging.getLogger(__name__)


class FlightCache:
    def __init__(self):
        sentinel = Sentinel([(os.environ['REDIS_SENTINEL_HOST'], 26379)], socket_timeout=0.3)
        self.redis = sentinel.master_for('flights', socket_timeout=0.3)
        self.pending = set()

    def get(self, key, message_type, load):
        try:
            self.flush_invalidations()
            value = self.redis.get(key)
            if value is not None:
                log.info('cache hit %s', key)
                return message_type.FromString(value)
        except RedisError as error:
            log.warning('Redis: %s', error)
            log.info('cache miss %s', key)
            return load()

        log.info('cache miss %s', key)
        message = load()
        try:
            self.redis.set(key, message.SerializeToString(), ex=300)
        except RedisError as error:
            log.warning('Redis: %s', error)
        return message

    def invalidate(self, *keys):
        self.pending.update(keys)
        try:
            self.flush_invalidations()
        except RedisError as error:
            log.warning('Redis: %s', error)

    def flush_invalidations(self):
        if self.pending:
            self.redis.delete(*self.pending)
            self.pending.clear()
