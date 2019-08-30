import numpy
import simpy
from block import Block, sha256
from persistence import *
from network import Socket, Link, Event


class Miner(object):

    # Define action names
    BLOCK_REQUEST = 1 # Hey! I need a block!
    BLOCK_RESPONSE = 2 # Here is the block you wanted!
    HEAD_NEW = 3 # I have a new chain head!
    BLOCK_NEW = 4 # Just mined a new block!


    # Network block rate a.k.a 1 block every ten minutes
    BLOCK_RATE = 1.0 / 600.0
    # A miner is able to verify 200KBytes per seconds
    VERIFY_RATE = 200*1024

    def __init__(self, env, store, hashrate, verifyrate, seed_block):
        # Simulation environment
        self.env = env
        # Get miner id from redis
        self.id = self.get_id()
        # print(self.id)
        # Socket
        self.socket = Socket(env, store, self.id, self.name)
        # Miner computing percentage of total network
        self.hashrate = hashrate
        # Miner block erification rate
        self.verifyrate = verifyrate
        # Store seed block
        self.seed_block = seed_block
        # Pointer to the block chain head
        self.chain_head = '*'
        # Hash with all the blocks the miner knows about
        self.blocks = dict()
        # Array with blocks needed to be processed
        self.blocks_new = []
        # Create event to notify when a block is mined
        self.block_mined = env.event()
        # Create event to notify when a new block arrives
        self.block_received = env.event()
        # Create event to notify when the mining process can continue
        self.continue_mining = env.event()
        self.mining = None
        # Store the miner in the database
        self.store()
        self.total_blocks = 0

    def __getattr__(self, name):
        print("Creating attribute %s."%name)
        setattr(self, name, 'default')

    def get_id(self):
        return get_id("miners")

    def store(self):
        key = "miners:" + str(self.id)
        r.hmset(key, {"hashrate": self.hashrate / Miner.BLOCK_RATE, "verifyrate": self.verifyrate})
        r.sadd("miners", self.id)


    def start(self):
        # Add the seed_block
        self.add_block(self.seed_block)
        # Start the process of adding blocks
        self.env.process(self.wait_for_new_block())
        # Receive network events
        self.env.process(self.receive_events())
        # Start mining and store the process so it can be interrupted
        self.mining = self.env.process(self.mine_block())

    def mine_block(self):
        # Indefinitely mine new blocks
        while True:
            try:
                # Determine block size
                block_size = 1024*200*numpy.random.random()
                # Determine the time the block will be mined depending on the miner hashrate
                time = numpy.random.exponential(1/self.hashrate, 1)[0]
                # Wait for the block to be mined
                yield self.env.timeout(time)
                # Once the block is mined it needs to be added. An event is triggered
                block = Block(
                    prev=self.chain_head,
                    height=self.blocks[self.chain_head].height + 1,
                    time=self.env.now,
                    miner_id=self.id,
                    miner_name=self.name,
                    size=block_size,
                    valid=1)
                self.notify_new_block(block)
            except simpy.Interrupt as i:
                # When the mining process is interrupted it cannot continue until it is told to continue
                yield self.continue_mining

    def notify_new_block(self, block):
        self.total_blocks += 1
        print("height  = {}, name = {}, valid = {}, time = {}, hash = {}".format(
            block.height,
            self.name,
            (block.valid == 1),
            round(self.env.now, 4),
            sha256(block)))
        self.block_mined.succeed(block)
        # Create a new mining event
        self.block_mined = self.env.event()

    def notify_received_block(self, block):
        self.block_received.succeed(block)
        # Create a new block received event
        self.block_received = self.env.event()

    def stop_mining(self):
        self.mining.interrupt()

    def keep_mining(self):
        self.continue_mining.succeed()
        self.continue_mining = self.env.event()

    def add_block(self, block):
        # Add the seed block to the known blocks
        self.blocks[sha256(block)] = block
        # Store the block in redis
        r.zadd("miners:" + str(self.id) + ":blocks", block.height, sha256(block))
        # Announce block if chain_head isn't empty
        if self.chain_head == "*":
            self.chain_head = sha256(block)
        # If block height is greater than chain head, update chain head and announce new head
        if (block.height > self.blocks[self.chain_head].height):
            self.chain_head = sha256(block)
            self.announce_block(self.chain_head)

    def wait_for_new_block(self):
        while True:
            # Wait for a block to be mined or received
            blocks = yield self.block_mined | self.block_received
            # Interrupt the mining process so the block can be added
            self.stop_mining()
            #print("%d \tI stop mining" % self.id)
            for event, block in blocks.items():
                # print("%s ||| Miner %s - mined block at %7.4f" %(self.name, block.miner_name, self.env.now))
                # Add the new block to the pending ones
                self.blocks_new.append(block)
                # Process new blocks
            yield self.env.process(self.process_new_blocks())
            # Keep mining
            self.keep_mining()

    def verify_block(self, block):
        # If block was mined by the miner but the previous block is not the chain head it will not be valid
        if block.miner_id == self.id and block.prev != self.chain_head:
            return -1
        # If the previous block is not in miner blocks it is not possible to validate current block
        if block.prev not in self.blocks:
            return 0
        # If block height isnt previous block + 1 it will not be valid
        if block.height != self.blocks[block.prev].height + 1:
            return -1
        return 1

    def process_new_blocks(self):
        blocks_later = []
        # Validate every new block
        for block in self.blocks_new:
            # Block validation takes some time
            yield self.env.timeout(block.size / self.verifyrate)
            valid = self.verify_block(block)
            if valid == 1:
                self.add_block(block)
            elif valid == 0:
                #Logger.log(self.env.now, self.id, "NEED_DATA", sha256(block))
                self.request_block(block.prev)
                blocks_later.append(block)
        self.blocks_new = blocks_later

    # Announce new head when block is added to the chain
    def announce_block(self, block):
        if self.id == 8:
            print("Announce %s - %s" %(block, self.blocks[block].miner_id))
        self.broadcast(Miner.HEAD_NEW, block)

    # Request a block to all links
    def request_block(self, block, to=None):
        # print(self.env.now, self.name, "REQUEST", block)
        if to is None:
            self.broadcast(Miner.BLOCK_REQUEST, block)
        else:
            self.send_event(to, Miner.BLOCK_REQUEST, block)

    # Send a block to a specific miner
    def send_block(self, block_hash, to):
        # Find the block
        block = self.blocks[block_hash]
        # Send the event
        self.send_event(to, Miner.BLOCK_RESPONSE, block)

    # Send certain event to a specific miner
    def send_event(self, to, action, payload):
        self.socket.send_event(to, action, payload)

    # Broadcast an event to all links
    def broadcast(self, action, payload):
        self.socket.broadcast(action, payload)

    def receive_events(self):
        while True:
            # Wait for a network event
            if len(self.socket.links) == 0:
                return
            data = yield self.socket.receive(self.id)
            if data.action == Miner.BLOCK_REQUEST:
                # Send block if we have it
                if data.payload in self.blocks:
                    self.send_block(data.payload, data.origin)
            elif data.action == Miner.BLOCK_RESPONSE:
                self.notify_received_block(data.payload)
            elif data.action == Miner.HEAD_NEW:
                # If we don't have the new head, we need to request it
                if data.payload not in self.blocks:
                    self.request_block(data.payload)

            #print("Miner %d - receives block %d at %7.4f" %(self.id, sha256(data), self.env.now))

    def add_link(self, destination, delay):
        link = Link(self.id, destination, delay)
        self.socket.add_link(link)
        r.sadd("miners:" + str(self.id) + ":links", link.id)

    @staticmethod
    def connect(miner, other_miner):
        miner.add_link(other_miner.id, 0.02)
        other_miner.add_link(miner.id, 0.02)


class HonestMiner(Miner):
    # An Honest miner
    def __init__(self, env, store, hashrate, verifyrate, seed_block):
        self.name = 'hon'
        super(HonestMiner, self).__init__( env, store, hashrate, verifyrate, seed_block)

    def add_block(self, block):
        # Add the seed block to the known blocks
        self.blocks[sha256(block)] = block
        # Store the block in redis
        r.zadd("miners:" + str(self.id) + ":blocks", block.height, sha256(block))
        # Announce block if chain_head isn't empty
        if self.chain_head == "*":
            self.chain_head = sha256(block)
        # If block height is greater than chain head and valid, update chain head and announce new head
        if (block.height > self.blocks[self.chain_head].height) and block.valid:
            self.chain_head = sha256(block)
            self.announce_block(self.chain_head)


class SPVMiner(Miner):
    # An SPV miner
    def __init__(self, env, store, hashrate, verifyrate, seed_block, val_time):
        self.chain_head_others = "*"
        self.private_branch_len = 0
        self.val_time = val_time
        self.name = 'spv'
        super(SPVMiner, self).__init__( env, store, hashrate, verifyrate, seed_block)

    def mine_block(self):
        # Indefinitely mine new blocks
        while True:
            try:
                # SPV miner blocksize is 0 (empty)
                block_size = 0
                # Determine the time the block will be mined depending on the miner hashrate
                time = numpy.random.exponential(1/self.hashrate, 1)[0]
                # Wait for the block to be mined
                yield self.env.timeout(time)
                # If chain_head is valid then our block on top is valid, else invalid
                if self.blocks[self.chain_head].valid == 1:
                    valid = 1
                else:
                    valid = 0
                # create block
                block = Block(
                    prev=self.chain_head,
                    height=self.blocks[self.chain_head].height + 1,
                    time=self.env.now,
                    miner_id=self.id,
                    miner_name=self.name,
                    size=block_size,
                    valid=valid)
                # Once the block is mined it needs to be added. An event is triggered
                self.notify_new_block(block)
            except simpy.Interrupt as i:
                # When the mining process is interrupted it cannot continue until it is told to continue
                yield self.continue_mining

    def process_new_blocks(self):
        blocks_later = []
        # Validate every new block
        for block in self.blocks_new:
            # Block validation takes shorter times from SPV mining behavior
            if self.val_time > 0:
                # Multiply val_time by some timeout
                yield self.env.timeout(self.val_time * (block.size / self.verifyrate))
            valid = self.verify_block(block)
            if valid == 1:
                self.add_block(block)
            elif valid == 0:
                #Logger.log(self.env.now, self.id, "NEED_DATA", sha256(block))
                self.request_block(block.prev)
                blocks_later.append(block)
        self.blocks_new = blocks_later


class AttackMiner(Miner):
    WIN = 5
    LOSE = 6

    # A Malicious miner
    def __init__(self, env, store, hashrate, verifyrate, seed_block, tgt_cfrms):
        self.name = 'att'
        self.chain_head_others = "*"
        self.tgt_cfrms = tgt_cfrms
        self.invalid_len = 0
        self.honest_len = 0
        # Create event to notify when attacker wins
        self.win = env.event()
        # Create event to notify when attacker loses
        self.lose = env.event()
        super(AttackMiner, self).__init__( env, store, hashrate, verifyrate, seed_block)

    def add_block(self, block):
        # Add the seed block to the known blocks
        self.blocks[sha256(block)] = block
        # Store the block in redis
        r.zadd("miners:" + str(self.id) + ":blocks", block.height, sha256(block))
        # Announce block if chain_head isn't empty
        if self.chain_head == "*":
            self.chain_head = sha256(block)
            self.chain_head_others = sha256(block)

        if not block.valid:
            if block.height > self.blocks[self.chain_head].height:
                self.chain_head = sha256(block)
                self.invalid_len += 1
                self.announce_block(self.chain_head)
        else:
            if block.height > self.blocks[self.chain_head_others].height:
                self.chain_head_others = sha256(block)
                # if attacker has already forked to invalid chain, we increment the counter
                if self.invalid_len > 0:
                    # print('Honest network found higher block with attacker started')
                    self.honest_len += 1
                    # if honest network receives "k" confirmations first, we reset
                    if self.honest_len == self.tgt_cfrms:
                        self.lose.succeed()
                        self.chain_head = sha256(block)
                        print('att - new chain head = {}, height = {}'.format(self.chain_head, self.honest_len))
                        self.announce_block(self.chain_head)
                else:
                    if block.height > self.blocks[self.chain_head].height:
                        self.chain_head = sha256(block)
                        print('att - new chain head = {}, height = {}'.format(self.chain_head, self.honest_len))
                        self.announce_block(self.chain_head)
        # if the attacker gets the final block for target confirmations here, reset values
        if self.invalid_len == self.tgt_cfrms or self.honest_len == self.tgt_cfrms:
            self.win.succeed()
            self.honest_len = 0
            self.invalid_len = 0

    def mine_block(self):
        # Indefinitely mine new blocks
        while True:
            try:
                block_size = 1024*200*numpy.random.random()
                # Determine the time the block will be mined depending on the miner hashrate
                time = numpy.random.exponential(1/self.hashrate, 1)[0]
                # Wait for the block to be mined
                yield self.env.timeout(time)
                # create invalid block
                block = Block(
                    prev=self.chain_head,
                    height=self.blocks[self.chain_head].height + 1,
                    time=self.env.now,
                    miner_id=self.id,
                    miner_name=self.name,
                    size=block_size,
                    valid=0)
                # Once the block is mined it needs to be added. An event is triggered
                self.notify_new_block(block)
            except simpy.Interrupt as i:
                # When the mining process is interrupted it cannot continue until it is told to continue
                yield self.continue_mining
