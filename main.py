import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.cm as cm
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
    LOGGING_MODE = "debug"

    @staticmethod
    def standard(miners_number=20, days=10):
        # Convert simulation days to seconds
        simulation_time = moment.get_seconds(days)
        try:
            print('here')
            clear_db()
            print('here')
        except ConnectionError:
            print('here')
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
    def mixed_spv_attack(alpha=0.5, beta=0.5, days=10, target_confirmations=3, tSPV=0.5):
        print("alpha - {}, beta = {}, days - {}, tgt - {}, tSPV - {}".format(
            alpha,
            beta,
            days,
            target_confirmations,
            tSPV))
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
        other_agents = [honest_miner]
        Miner.connect(honest_miner, attack_miner)
        if alpha + beta < 1.0:
            # fraction of normal validation time to spend
            spv_miner = SPVMiner(env, store, (1.0 - alpha - beta) * Miner.BLOCK_RATE, Miner.VERIFY_RATE, seed_block, tSPV)
            miners.append(spv_miner)
            other_agents.append(spv_miner)
            Miner.connect(honest_miner, spv_miner)
            Miner.connect(attack_miner, spv_miner)

        attack_miner.set_agents(other_agents)
        print('miner powers: [honest, attack, (spv)]: {}'.format(list(map(lambda x: x.hashrate, miners))))
        for miner in miners: miner.start()
        start = time.time()
        # Start simulation until limit. Time unit is seconds
        env.run(until=simulation_time)
        end = time.time()
        if Simulator.LOGGING_MODE == "debug":
            print("Simulation took: %1.4f seconds" % (end - start))
            print(attack_miner.wins, attack_miner.loses)
        # Store in redis simulation days
        store_days(days)
        # for miner in miners: print(miner.name, miner.blocks[miner.chain_head].height, miner.chain_head)
        # After simulation store every miner head, so their chain can be built again
        for miner in miners: r.hset("miners:" + repr(miner.id), "head", miner.chain_head)
        # Notify simulation ended
        r.publish(Simulator.PUBSUB_CHANNEL, Simulator.SIMULATION_ENDED)
        return (attack_miner.wins, attack_miner.loses)


def run_plain_simulation_with_varying_gamma(alpha=0.5, max_num_conf=21, days=10):
    plt.clf()
    fig, ax = plt.subplots()
    for inx, beta in enumerate([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]):
        if alpha + beta > 1.0:
            continue
        # Simulator.standard(3, 1)
        xs, ys = [], []
        for i, elt in enumerate(range(1, max_num_conf)):
            wins, loses = Simulator.mixed_spv_attack(round(alpha, 2), round(beta, 2), days * (elt + 1), elt)
            xs.append(elt)
            ys.append(wins * 1.0 / (loses + wins))
        ax.plot(xs, ys, label='β = {}, γ = {}'.format(round(beta, 2), round(1 - alpha - beta, 2)))
    ax.set_title('Success probability with α = {}'.format(alpha))
    ax.set_xlabel('Number of confirmations')
    ax.set_ylabel('Probabilitiy of success')
    ax.set_xticks(numpy.arange(1, max_num_conf, 1))
    ax.set_yticks(numpy.arange(0, 1.0, 0.1))
    plt.grid()
    plt.legend()
    plt.savefig('artifacts/cont-graph-{}.png'.format(alpha))

def run_mixed_sim_with_varying_attack_env(alpha, beta, gamma, max_num_conf=21, days=1):
    if alpha + beta + gamma > 1.0: raise ValueError("Invalid setup of power")
    plt.clf()
    fig, ax = plt.subplots()
    setup1 = [alpha, beta, gamma, 0.0]
    setup2 = [alpha + gamma, beta, 0.0, 0.0]
    setup3 = [alpha, beta + gamma, 0.0, 0.0]
    # setup4 = [alpha, beta, gamma, 1.0]
    # setup5 = [alpha + gamma, beta, 0.0, 1.0]
    # setup6 = [alpha, beta + gamma, 0.0, 1.0]
    setups = [
        setup1,
        setup2,
        setup3,
        # setup4,
        # setup5,
        # setup6
    ]
    for inx, s in enumerate(setups):
        xs, ys = [], []
        for i, elt in enumerate(range(1, max_num_conf)):
            wins, loses = Simulator.mixed_spv_attack(
                alpha=s[0],
                beta=s[1],
                days=10 * days * (elt + 1),
                target_confirmations=elt,
                tSPV=s[3])
            xs.append(elt)
            ys.append(wins * 1.0 / (loses + wins))
        print(xs, ys)
        ax.plot(xs, ys, label='β = {}, γ = {}'.format(s[1], s[2]))
    ax.set_title('Success probability with varying agent environments')
    ax.set_xlabel('Number of confirmations')
    ax.set_ylabel('Probabilitiy of success')
    ax.set_xticks(numpy.arange(1, max_num_conf, 1))
    ax.set_yticks(numpy.arange(0, 1.0, 0.1))
    plt.grid()
    plt.legend()
    plt.savefig('artifacts/mixed-graph-{}-{}.png'.format(alpha, beta))    

if __name__ == '__main__':
    Simulator.mixed_spv_attack(0.2, 0.5, 1, 6, 1.0)
    # run_plain_simulation_with_varying_gamma(0.2)
    # run_mixed_sim_with_varying_attack_env(0.2, 0.4, 0.3)
