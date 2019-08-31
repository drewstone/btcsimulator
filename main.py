from persistence import *
from block import Block
from miner import Miner, HonestMiner, SPVMiner, AttackMiner
import moment
import simpy
import time
import numpy


class Simulator:

    SIMULATION_ENDED = "SIMULATION_ENDED"
    PUBSUB_CHANNEL = "/btcsimulator"
    LOGGING_MODE = "none"

    @staticmethod
    def standard(miners_number=20, days=10):
        # Convert simulation days to seconds
        simulation_time = moment.get_seconds(days)
        try:
            clear_db()
        except ConnectionError:
            return -1
        # Store in redis the simulation event names
        configure_event_names([Miner.BLOCK_REQUEST, Miner.BLOCK_RESPONSE, Miner.BLOCK_NEW, Miner.HEAD_NEW])
        # Create simpy environment
        env = simpy.Environment()
        store = simpy.FilterStore(env)
        # Create the seed block
        seed_block = Block(None, 0, env.now, -1, 0, 1)
        hashrates = numpy.random.dirichlet(numpy.ones(miners_number), size=1)
        # Create miners
        miners = []
        # This dict is used to store the connections between miners, so they are not created twice
        connections = dict()
        for i in range(0, miners_number):
            miner = Miner(env, store, hashrates[0,i] * Miner.BLOCK_RATE, Miner.VERIFY_RATE, seed_block)
            miners.append(miner)
            connections[miner] = dict()
        # Randomly connect miners
        for i, miner in enumerate(miners):
            miner_connections = numpy.random.choice([True, False], miners_number)
            for j, miner_connection in enumerate(miner_connections):
                # Onlye create connection if miner is not self and connection does not already exist
                if i != j and miner_connection == True and j not in connections[miner] and i not in connections[miners[j]]:
                    # Store connection so its not created twice
                    connections[miner][j] = True
                    connections[miners[j]][i] = True
                    Miner.connect(miner, miners[j])
        for miner in miners: miner.start()
        start = time.time()
        # Start simulation until limit. Time unit is seconds
        env.run(until=simulation_time)
        end = time.time()
        print("Simulation took: %1.4f seconds" % (end - start))
        # Store in redis simulation days
        store_days(days)
        for miner in miners: print(miner.blocks[miner.chain_head].height, miner.chain_head)
        # After simulation store every miner head, so their chain can be built again
        for miner in miners: r.hset("miners:" + repr(miner.id), "head", miner.chain_head)
        # Notify simulation ended
        r.publish(Simulator.PUBSUB_CHANNEL, Simulator.SIMULATION_ENDED)
        return 0

    @staticmethod
    def mixed_spv_attack(alpha=0.5, beta=0.5, days=10, target_confirmations=3):
        if (alpha + beta > 1.0): raise ValueError("Invalid power fractions")
        # Convert simulation days to seconds
        simulation_time = moment.get_seconds(days)
        try:
            clear_db()
        except ConnectionError:
            return -1
        # Store in redis the simulation event names
        configure_event_names([
            Miner.BLOCK_REQUEST,
            Miner.BLOCK_RESPONSE,
            Miner.BLOCK_NEW,
            Miner.HEAD_NEW,
            AttackMiner.WIN,
            AttackMiner.LOSE
        ])
        # Create simpy environment
        env = simpy.Environment()
        store = simpy.FilterStore(env)
        # Create the seed block
        seed_block = Block(None, 0, env.now, -1, 'seed', 0, 1)
        # Create miners
        miners = []
        # This dict is used to store the connections between miners, so they are not created twice
        honest_miner = HonestMiner(env, store, beta * Miner.BLOCK_RATE, Miner.VERIFY_RATE, seed_block)
        attack_miner = AttackMiner(env, store, alpha * Miner.BLOCK_RATE, Miner.VERIFY_RATE, seed_block, target_confirmations)
        miners = [honest_miner, attack_miner]
        Miner.connect(honest_miner, attack_miner)
        if alpha + beta < 1.0:
            # fraction of normal validation time to spend
            val_time = 0.1
            spv_miner = SPVMiner(env, store, (1.0 - alpha - beta) * Miner.BLOCK_RATE, Miner.VERIFY_RATE, seed_block, val_time)
            miners.append(spv_miner)
            Miner.connect(honest_miner, spv_miner)
            Miner.connect(attack_miner, spv_miner)

        for miner in miners: miner.start()
        start = time.time()
        # Start simulation until limit. Time unit is seconds
        env.run(until=simpy.events.AnyOf(env, [
            attack_miner.win,
            attack_miner.lose,
        ]))
        end = time.time()
        if Simulator.LOGGING_MODE == "debug": print("Simulation took: %1.4f seconds" % (end - start))
        # Store in redis simulation days
        store_days(days)
        # for miner in miners: print(miner.name, miner.blocks[miner.chain_head].height, miner.chain_head)
        # After simulation store every miner head, so their chain can be built again
        for miner in miners: r.hset("miners:" + repr(miner.id), "head", miner.chain_head)
        # Notify simulation ended
        r.publish(Simulator.PUBSUB_CHANNEL, Simulator.SIMULATION_ENDED)
        return (
            honest_miner.blocks[honest_miner.chain_head].height,
            attack_miner.blocks[attack_miner.chain_head].height
        )


if __name__ == '__main__':
    # Simulator.standard(3, 1)
    wins, loses = 0, 0
    for i in range(1000):
        hon_ht, att_ht = Simulator.mixed_spv_attack(0.3, 0.6, 1, 10)
        if hon_ht < att_ht:
            wins += 1
        else:
            loses += 1

    print(wins, loses, wins * 1.0 / (wins + loses))
