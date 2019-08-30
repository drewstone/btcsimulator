import hashlib
import pickle
from persistence import *


def sha256(data):
    return hashlib.sha256(pickle.dumps(data)).hexdigest()


class Block:
    def __init__(self, prev=None, height=None, time=None, miner_id=None, miner_name=None, size=None, valid=None):
        self.prev = prev
        self.height = height
        self.time = time
        self.miner_id = miner_id
        self.miner_name = miner_name
        self.size = size
        self.valid = valid
        # When a block is created it is stored in redis
        self.store()

    def store(self):
        key = 'blocks:' + str(sha256(self))
        # Store block in block list
        r.zadd("blocks", self.height, sha256(self))
        # Store the block info
        r.hmset(key, {'prev': self.prev, 'height':self.height, 'time': self.time, 'size': self.size, 'valid': self.valid, 'miner': self.miner_id})
        # Store reference block in the miner's blocks set
        r.zadd("miners:" + str(self.miner_id) + ":blocks-mined", self.height, sha256(self))

    def __str__(self):
        return "{}, {}, {}, {}, {}".format(self.height, self.time, self.miner_name, self.valid, str(sha256(self)))