import cProfile
import logging
import random
import time
from datetime import datetime
from itertools import groupby
from typing import Dict, NamedTuple

import zmq
from zmq import Context

from dsm.epaxos.command.deps.default import DefaultDepsStore
from dsm.epaxos.command.state import AbstractCommand
from dsm.epaxos.instance.store import InstanceStore
from dsm.epaxos.network.zeromq.mapping import deserialize, ZMQReplicaReceiveChannel, ZMQReplicaSendChannel, \
    ZMQClientSendChannel
from dsm.epaxos.network.zeromq.util import cli_logger
from dsm.epaxos.replica.replica import Replica
from dsm.epaxos.replica.state import ReplicaState
from dsm.epaxos.timeout.store import TimeoutStore

logger = logging.getLogger(__name__)


class ReplicaAddress(NamedTuple):
    replica_addr: str


class ReplicaServer:
    def __init__(
        self,
        context: Context,
        epoch: int,
        replica_id: int,
        peer_addr: Dict[int, ReplicaAddress],
    ):
        self.poller = zmq.Poller()
        self.peer_addr = peer_addr

        socket = context.socket(zmq.ROUTER)
        socket.bind(peer_addr[replica_id].replica_addr)
        socket.setsockopt_string(zmq.IDENTITY, str(replica_id))
        socket.setsockopt(zmq.ROUTER_HANDOVER, 1)

        self.poller.register(socket, zmq.POLLIN)
        self.socket = socket

        self.channel_receive = ZMQReplicaReceiveChannel(self)
        self.channel_send = ZMQReplicaSendChannel(self)

        state = ReplicaState(
            self.channel_send,
            epoch, replica_id,
            set(peer_addr.keys()),
            set(peer_addr.keys()),
            True,
            5
        )

        deps_store = DefaultDepsStore()
        timeout_store = TimeoutStore(state)
        store = InstanceStore(state, deps_store, timeout_store)

        self.state = state
        self.replica = Replica(state, store)

    def connect(self):
        logger.info(f'Replica `{self.state.replica_id}` connecting.')
        for peer_id, addr in self.peer_addr.items():
            if peer_id == self.state.replica_id:
                continue

            self.socket.connect(addr.replica_addr)

    def main(self):
        logger.info(f'Replica `{self.state.replica_id}` started.')

        last_tick_time = datetime.now()
        poll_delta = 0.
        last_tick = self.state.ticks

        while True:
            min_wait = self.replica.check_timeouts_minimum_wait()
            min_wait_poll = max(0, self.state.seconds_per_tick - poll_delta)

            if min_wait:
                min_wait = min(min_wait, min_wait_poll)
            else:
                min_wait = min_wait_poll

            poll_result = self.poller.poll(min_wait * 1000.)
            poll_delta = (datetime.now() - last_tick_time).total_seconds()

            if poll_delta > self.state.seconds_per_tick:
                self.replica.tick()
                self.replica.check_timeouts()
                last_tick_time = datetime.now()

            sockets = dict(poll_result)

            if self.socket in sockets:
                packets = []
                while True:
                    try:
                        replica_request = self.socket.recv_multipart(flags=zmq.NOBLOCK)[-1]

                        packets.append(replica_request)
                    except zmq.ZMQError:
                        break
                for packet in packets:
                    self.channel_receive.receive_packet(packet)

                if len(packets):
                    self.replica.execute_pending()
            else:
                pass

            if self.state.ticks != last_tick and self.state.ticks % (self.state.jiffies * 30) == 0:
                last_tick = self.state.ticks
                print(
                    datetime.now(),
                    self.state.ticks,
                    self.state.seconds_per_tick,
                    self.state.replica_id,
                    sorted((y.name, len(list(x))) for y, x in
                           groupby(sorted([v.type for k, v in self.replica.store.instances.items()]))),
                    sorted((k, v) for k, v in self.replica.store.executed_cut.items())
                )


class ReplicaClient:
    def __init__(
        self,
        context: Context,
        peer_id: int,
        peer_addr: Dict[int, ReplicaAddress],

    ):
        self.peer_id = peer_id
        self.peer_addr = peer_addr
        self.replica_id = None
        self.poller = zmq.Poller()

        socket = context.socket(zmq.DEALER)
        socket.setsockopt_string(zmq.IDENTITY, str(peer_id))

        self.socket = socket
        self.poller.register(self.socket, zmq.POLLIN)

        self.channel = None

    def connect(self, replica_id=None):
        if replica_id is None:
            replica_id = random.choice(list(self.peer_addr.keys()))

        if self.replica_id:
            self.socket.disconnect(self.peer_addr[self.replica_id].replica_addr)

        self.replica_id = replica_id
        self.channel = ZMQClientSendChannel(self)

        self.socket.connect(self.peer_addr[replica_id].replica_addr)

    def request(self, command: AbstractCommand):
        assert self.replica_id is not None

        TIMEOUT = 1000

        self.channel.client_request(self.replica_id, command)

        start = datetime.now()

        while True:
            poll_result = dict(self.poller.poll(TIMEOUT))

            if self.socket in poll_result:
                payload, = self.socket.recv_multipart()

                rtn = deserialize(payload)
                # logger.info(f'Client `{self.peer_id}` -> {self.replica_id} Send={command} Recv={rtn.payload}')

                end = datetime.now()
                latency = (end - start).total_seconds()
                return latency, rtn
            else:
                # logger.info(f'Client `{self.peer_id}` -> {self.replica_id} RetrySend={command}')
                self.channel.client_request(self.replica_id, command)


def replica_server(epoch: int, replica_id: int, replicas: Dict[int, ReplicaAddress]):
    profile = True
    if profile:
        pr = cProfile.Profile()
        pr.enable()
    # print('Calibrating profiler')
    # for i in range(5):
    #     print(pr.calibrate(10000))
    try:
        cli_logger()
        context = zmq.Context(len(replicas))
        rs = ReplicaServer(context, epoch, replica_id, replicas)
        rs.connect()

        rs.main()

    except:
        logger.exception(f'Server {replica_id}')
    finally:
        if profile:
            pr.disable()
            pr.dump_stats(f'{replica_id}.profile')


def replica_client(peer_id: int, replicas: Dict[int, ReplicaAddress]):
    try:
        cli_logger()
        context = zmq.Context()
        rc = ReplicaClient(context, peer_id, replicas)
        rc.connect()

        time.sleep(0.5)

        latencies = []

        for i in range(20000):
            lat, _ = rc.request(AbstractCommand(random.randint(1, 1000000)))
            # latencies.append(lat)
            if i % 200 == 0:
                # print(latencies)
                logger.info(f'Client `{peer_id}` DONE {i + 1}')
        logger.info(f'Client `{peer_id}` DONE')
    except:
        logger.exception(f'Client {peer_id}')
