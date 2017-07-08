from typing import List

from dsm.epaxos.command.state import AbstractCommand
from dsm.epaxos.instance.state import Slot, Ballot, State


class AcceptorInterface:
    def pre_accept_request(self, peer: int, slot: Slot, ballot: Ballot, command: AbstractCommand,
                           seq: int,
                           deps: List[Slot]):
        raise NotImplementedError()

    def accept_request(self, peer: int, slot: Slot, ballot: Ballot, command: AbstractCommand, seq: int,
                       deps: List[Slot]):
        raise NotImplementedError()

    def commit_request(self, peer: int, slot: Slot, ballot: Ballot, seq: int, command: AbstractCommand,
                       deps: List[Slot]):
        raise NotImplementedError()

    def prepare_request(self, peer: int, slot: Slot, ballot: Ballot):
        raise NotImplementedError()


class LeaderInterface:
    def client_request(self, client_peer: int, command: AbstractCommand):
        raise NotImplementedError()

    def pre_accept_response_ack(self, peer: int, slot: Slot, ballot: Ballot, seq: int, deps: List[Slot]):
        raise NotImplementedError()

    def pre_accept_response_nack(self, peer: int, slot: Slot):
        raise NotImplementedError()

    def prepare_response_ack(self, peer: int, slot: Slot, ballot: Ballot, command: AbstractCommand,
                             seq: int, deps: List[Slot], state: State):
        raise NotImplementedError()

    def prepare_response_nack(self, peer: int, slot: Slot):
        raise NotImplementedError()


class ClientInterface:
    def client_response(self, client_peer: int, command: AbstractCommand):
        raise NotImplementedError()


class Channel(AcceptorInterface, ClientInterface, LeaderInterface):
    def __init__(self):
        pass
